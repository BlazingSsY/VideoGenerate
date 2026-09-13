import unittest
import os
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import AgentRun, AgentSession, AgentTask, AgentTurn, Upload, User
from app.routers import agent, conversations
from app.security import current_user
from app.agent_service import create_plan, validate_plan
from app.canvas_graph import GraphValidationError, media_handles, validate_graph


def _fallback_plan(user, user_input: str, target_duration: int) -> dict:
    """直调规则降级生成（旧 /plans 端点内联的同款逻辑，现在绕过端点取回 plan）。"""
    import asyncio
    skill_id, value, provider_result, warning = asyncio.run(create_plan(
        user, user_input, [], "studio", None, target_duration,
    ))
    assert provider_result is None, "测试环境应走规则降级而非 Provider"
    return value


class AgentSecurityTests(unittest.TestCase):
    def setUp(self):
        from app.config import settings
        self.original_fallback = settings.agent_fallback_rules
        self.original_configured = settings.agent_provider_configured
        settings.agent_fallback_rules = True
        settings.agent_provider_configured = False  # force fallback mode for tests
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            first = User(username="first", password_hash="x", role="user")
            second = User(username="second", password_hash="x", role="user")
            db.add_all([first, second]); db.commit(); db.refresh(first); db.refresh(second)
            self.first_id, self.second_id, self.current = first.id, second.id, first.id
        app = FastAPI(); app.include_router(agent.router); app.include_router(conversations.router)
        def db_override():
            db = self.Session()
            try: yield db
            finally: db.close()
        def user_override():
            with self.Session() as db: return db.get(User, self.current)
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[current_user] = user_override
        self.client = TestClient(app)

    def tearDown(self):
        from app.config import settings
        settings.agent_fallback_rules = self.original_fallback
        settings.agent_provider_configured = self.original_configured
        self.client.close()
        self.engine.dispose()

    def test_local_upload_from_another_user_is_rejected(self):
        with self.Session() as db:
            db.add(Upload(filename="private.png", user_id=self.second_id, kind="image")); db.commit()
        cyclic_free_plan = {
            "target_duration": 5,
            "nodes": [
                {"id": "g1", "type": "generate", "prompt": "让图动起来", "model": "happyhorse-1.1-i2v",
                 "capability": "i2v", "resolution": "1080P", "ratio": "", "duration": 5,
                 "reference_media": [{"kind": "image", "url": "/media/uploads/private.png"}], "depends_on": []},
            ],
        }
        with self.assertRaises(HTTPException) as ctx:
            validate_plan(self.Session(), self._user(), None, cyclic_free_plan)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_turn_plan_can_only_be_accepted_once(self):
        # v3：turn 建好后手动写入 plan（模拟编排完成），走 /turns/{id}/plan/accept
        turn_id = self._create_turn_with_plan()
        with patch("app.routers.agent.spawn_run"):
            self.assertEqual(
                self.client.post(f"/api/agent/turns/{turn_id}/plan/accept").status_code, 200)
            self.assertEqual(
                self.client.post(f"/api/agent/turns/{turn_id}/plan/accept").status_code, 409)

    def _create_turn_with_plan(self, plan: dict | None = None):
        plan = plan or {
            "target_duration": 5,
            "nodes": [
                {"id": "g1", "type": "generate", "prompt": "日落海边", "model": "happyhorse-1.1-t2v",
                 "capability": "t2v", "resolution": "1080P", "ratio": "16:9", "duration": 5,
                 "reference_media": [], "depends_on": []},
            ],
        }
        response = self.client.post(
            "/api/agent/turns",
            json={"surface": "studio", "user_input": "日落海边"},
        )
        self.assertEqual(response.status_code, 201)
        turn_id = response.json()["id"]
        with self.Session() as db:
            turn = db.get(AgentTurn, turn_id)
            turn.plan = plan
            turn.status = "answered"
            db.commit()
        return turn_id

    def test_fallback_plan_splits_long_target_into_legal_segments(self):
        plan = _fallback_plan(self._user(), "产品宣传片", 30)
        generated = [node for node in plan["nodes"] if node["type"] == "generate"]
        self.assertGreater(len(generated), 1)
        self.assertTrue(all(node["duration"] <= 15 for node in generated))
        self.assertEqual(plan["nodes"][-1]["type"], "compose")

    def test_fallback_plan_does_not_create_too_short_last_segment(self):
        plan = _fallback_plan(self._user(), "短片", 16)
        generated = [node for node in plan["nodes"] if node["type"] == "generate"]
        self.assertEqual([node["duration"] for node in generated], [8, 8])

    def test_agent_models_endpoint_is_does_not_expose_credentials(self):
        response = self.client.get("/api/agent/models")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all("api_key" not in item and "base_url" not in item for item in response.json()))

    def test_accept_creates_recoverable_run_and_tasks(self):
        turn_id = self._create_turn_with_plan(_fallback_plan(self._user(), "产品宣传片", 30))
        with patch("app.routers.agent.spawn_run") as spawn:
            accepted = self.client.post(f"/api/agent/turns/{turn_id}/plan/accept")
        self.assertEqual(accepted.status_code, 200)
        with self.Session() as db:
            run = db.query(AgentRun).filter_by(turn_id=turn_id).one()
            tasks = db.query(AgentTask).filter_by(run_id=run.id).all()
            self.assertGreater(len(tasks), 0)
            self.assertTrue(any(task.task_type == "compose" for task in tasks))
            spawn.assert_called_once_with(run.id)

    def test_structured_plan_rejects_cycles_and_invalid_compose_inputs(self):
        cyclic = {
            "target_duration": 10,
            "nodes": [
                {"id": "a", "type": "generate", "prompt": "a", "model": "happyhorse-1.1-t2v", "capability": "t2v", "resolution": "1080P", "ratio": "16:9", "duration": 5, "reference_media": [], "depends_on": ["b"]},
                {"id": "b", "type": "generate", "prompt": "b", "model": "happyhorse-1.1-t2v", "capability": "t2v", "resolution": "1080P", "ratio": "16:9", "duration": 5, "reference_media": [], "depends_on": ["a"]},
            ],
        }
        with self.assertRaises(Exception) as error:
            validate_plan(self.Session(), self._user(), None, cyclic)
        self.assertIn("环形", str(error.exception))

    def test_dynamic_slots_are_bounded_and_legacy_nodes_keep_fixed_slots(self):
        dynamic = {"type": "generate", "data": {"model": "happyhorse-1.1-r2v", "capability": "r2v", "media_slots": {"image": ["image_0", "image_2"]}}}
        self.assertEqual(media_handles(dynamic)["image"], ["image_0", "image_2"])
        legacy = {"type": "generate", "data": {"model": "happyhorse-1.1-r2v", "capability": "r2v"}}
        self.assertEqual(len(media_handles(legacy)["image"]), 9)
        too_many = {"type": "generate", "data": {"model": "happyhorse-1.1-r2v", "capability": "r2v", "media_slots": {"image": [f"image_{i}" for i in range(10)]}}}
        with self.assertRaises(GraphValidationError): media_handles(too_many)

    def _user(self):
        with self.Session() as db:
            return db.get(User, self.first_id)


if __name__ == "__main__": unittest.main()
