"""End-to-end control tests for Agent-owned canvas runs."""
import asyncio
import os
import sys
import tempfile
import time
import unittest
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.models import (
    AgentCanvasImport,
    AgentRun,
    AgentRunEvent,
    AgentSession,
    AgentTask,
    AgentTurn,
    Asset,
    Canvas,
    CanvasNode,
    Conversation,
    Message,
    User,
)
from app.routers import agent, canvas
from app.security import current_user


def two_shot_plan() -> dict:
    return {
        "title": "两镜头广告",
        "target_duration": 10,
        "nodes": [
            {
                "id": "shot-1", "type": "generate", "prompt": "产品全景",
                "model": "wan3.0-video-prime", "capability": "t2v",
                "resolution": "1080P", "ratio": "16:9", "duration": 5,
                "reference_media": [], "depends_on": [], "reason": "产品全景",
            },
            {
                "id": "shot-2", "type": "generate", "prompt": "产品近景",
                "model": "wan3.0-video-prime", "capability": "t2v",
                "resolution": "1080P", "ratio": "16:9", "duration": 5,
                "reference_media": [], "depends_on": [], "reason": "产品近景",
            },
            {
                "id": "compose-1", "type": "compose", "operation": "concat",
                "inputs": ["shot-1", "shot-2"],
                "depends_on": ["shot-1", "shot-2"],
            },
        ],
        "output_node": "compose-1",
        "reason": "顺序拼接",
    }


class AgentCanvasTakeoverTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.engine = create_engine(
            f"sqlite:///{directory}/test.db", connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        with self.Session() as db:
            user = User(username="agent-canvas-admin", password_hash="x", role="admin")
            db.add(user)
            db.flush()
            canvas_row = Canvas(id=uuid.uuid4().hex, user_id=user.id, title="Agent 画布")
            shadow = Conversation(id=canvas_row.id, user_id=user.id, title=canvas_row.title, kind="canvas")
            db.add_all([canvas_row, shadow])
            db.flush()
            session = AgentSession(user_id=user.id, surface="canvas", target_id=canvas_row.id)
            db.add(session)
            db.flush()
            turn = AgentTurn(
                session_id=session.id, user_input="做两镜头广告", status="answered",
                plan=two_shot_plan(),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            db.add(turn)
            db.commit()
            self.user_id = user.id
            self.canvas_id = canvas_row.id
            self.session_id = session.id
            self.turn_id = turn.id

        app = FastAPI()
        app.include_router(agent.router)
        app.include_router(canvas.router)

        def db_override():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        def user_override():
            with self.Session() as db:
                return db.get(User, self.user_id)

        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[current_user] = user_override
        self.client = self.enterContext(TestClient(app))
        self.patches = [
            patch("app.agent_chat.SessionLocal", self.Session),
            patch("app.agent_executor.SessionLocal", self.Session),
            patch("app.routers.agent.SessionLocal", self.Session),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.client.close()
        self.engine.dispose()

    def _accept(self):
        with patch("app.routers.agent.spawn_run"):
            response = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/accept")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["run_id"]

    def _patch_graph(self, operations):
        context = self.client.get(f"/api/canvases/{self.canvas_id}/agent/context").json()
        result = self.client.post(f"/api/canvases/{self.canvas_id}/agent/patch", json={
            "base_revision": context["revision"], "control_version": context["control_version"],
            "idempotency_key": uuid.uuid4().hex, "operations": operations,
        })
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_inflight_agent_patch_cannot_commit_after_takeover(self):
        from app import canvas_service

        imported = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/to-canvas").json()
        context = self.client.get(f"/api/canvases/{self.canvas_id}/agent/context").json()
        entered, release = threading.Event(), threading.Event()
        original = canvas_service.persist_canvas_graph

        def delayed(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args, **kwargs)

        payload = {
            "base_revision": context["revision"], "control_version": context["control_version"],
            "idempotency_key": "inflight", "operations": [{
                "op": "update_node", "node_id": imported["node_map"]["shot-1"],
                "data": {"inlinePrompt": "过期 Agent 写入"},
            }],
        }
        with patch.object(canvas_service, "persist_canvas_graph", delayed), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.client.post, f"/api/canvases/{self.canvas_id}/agent/patch", json=payload)
            try:
                self.assertTrue(entered.wait(5))
                takeover = self.client.post(f"/api/canvases/{self.canvas_id}/takeover")
                self.assertEqual(takeover.status_code, 200)
            finally:
                release.set()
            response = pending.result(timeout=5)
        self.assertEqual(response.status_code, 409, response.text)
        with self.Session() as db:
            self.assertEqual(db.get(CanvasNode, imported["node_map"]["shot-1"]).data["inlinePrompt"], "产品全景")
            self.assertEqual(db.get(Canvas, self.canvas_id).control_version, 1)

    def test_cached_canvas_cannot_overwrite_a_concurrent_graph_save(self):
        from app.canvas_service import CanvasServiceError, canvas_snapshot, persist_canvas_graph

        self.client.post(f"/api/agent/turns/{self.turn_id}/plan/to-canvas")
        with self.Session() as stale:
            canvas_row = stale.get(Canvas, self.canvas_id)
            graph = canvas_snapshot(canvas_row)
            node_id = graph["nodes"][0]["id"]
            self._patch_graph([{"op": "move_node", "node_id": node_id, "position": {"x": 1234, "y": 500}}])
            with self.assertRaises(CanvasServiceError) as raised:
                persist_canvas_graph(stale, canvas_row, graph, expected_revision=graph["revision"])
            self.assertEqual(raised.exception.status_code, 409)
        with self.Session() as db:
            self.assertEqual(db.get(CanvasNode, node_id).position["x"], 1234)

    def test_rewiring_prompt_rejects_old_plan(self):
        imported = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/to-canvas").json()
        node_id = imported["node_map"]["shot-1"]
        edge = next(edge for edge in imported["canvas"]["edges"] if edge["target"] == node_id and edge["target_handle"] == "prompt")
        self._patch_graph([
            {"op": "add_node", "node": {"id": "new-prompt", "type": "prompt", "position": {"x": 0, "y": 0}, "data": {"text": "新的提示词"}}},
            {"op": "disconnect", "edge_id": edge["id"]},
            {"op": "connect", "edge": {"id": "new-prompt-edge", "source": "new-prompt", "source_handle": "prompt", "target": node_id, "target_handle": "prompt"}},
        ])
        with patch("app.routers.agent.spawn_run") as spawn:
            response = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/accept")
        self.assertEqual(response.status_code, 409, response.text)
        spawn.assert_not_called()

    def test_changed_upstream_detaches_running_result_and_rejects_old_compose(self):
        from app import agent_executor as executor

        run_id = self._accept()
        message_id = executor._create_message(run_id, executor._load_task_node(run_id, "shot-2"))
        executor._set_task(run_id, "shot-2", status="running", message_id=message_id)
        with self.Session() as db:
            mapping = db.get(AgentRun, run_id).input_snapshot["node_map"]
        self._patch_graph([{"op": "update_node", "node_id": mapping["__prompts__"]["shot-2"], "data": {"text": "用户的新输入"}}])
        with self.Session() as db:
            message = db.get(Message, message_id)
            message.status = "succeeded"
            message.local_video = "obsolete.mp4"
            db.commit()
        executor._set_task(run_id, "shot-2", status="succeeded", output_file="obsolete.mp4", message_id=message_id)
        executor._set_task(run_id, "compose-1", status="succeeded", output_file="obsolete-joined.mp4")
        statuses = self.client.get(f"/api/canvases/{self.canvas_id}/status").json()
        for key in ("shot-2", "compose-1"):
            row = next(row for row in statuses if row["node_id"] == mapping[key])
            self.assertEqual(row["status"], "idle")
            self.assertFalse(row["video_src"])
            self.assertIsNone(row["message_id"])
        with self.Session() as db:
            self.assertEqual(db.query(AgentTask).filter_by(run_id=run_id, node_id="shot-2").one().status, "succeeded")

    def test_output_order_transitions_and_disconnection_reject_late_results(self):
        from app.agent_executor import _set_task

        run_id = self._accept()
        with self.Session() as db:
            mapping = db.get(AgentRun, run_id).input_snapshot["node_map"]
        context = self.client.get(f"/api/canvases/{self.canvas_id}/agent/context").json()
        output = next(node for node in context["nodes"] if node["id"] == mapping["compose-1"])
        for data in (
            {"items": list(reversed(output["data"]["items"]))},
            {"items": output["data"]["items"], "transitions": [{"after": mapping["shot-1"], "type": "fade"}]},
            {"transitions": [], "excluded": [mapping["shot-2"]]},
        ):
            with self.subTest(data=data):
                self._patch_graph([{"op": "update_node", "node_id": output["id"], "data": data}])
                _set_task(run_id, "compose-1", status="succeeded", output_file="obsolete.mp4")
                with self.Session() as db:
                    self.assertFalse(db.get(CanvasNode, output["id"]).data.get("outputFile"))
        self._patch_graph([{"op": "update_node", "node_id": output["id"], "data": {"excluded": []}}, {
            "op": "disconnect", "edge_id": next(edge["id"] for edge in context["edges"] if edge["target"] == output["id"]),
        }])
        _set_task(run_id, "compose-1", status="succeeded", output_file="obsolete.mp4")
        with self.Session() as db:
            self.assertFalse(db.get(CanvasNode, output["id"]).data.get("outputFile"))

    def test_layout_save_preserves_result_but_input_edit_removes_it(self):
        from app.agent_executor import _set_task

        run_id = self._accept()
        stale = self.client.get(f"/api/canvases/{self.canvas_id}").json()
        output = next(node for node in stale["nodes"] if node["type"] == "output")
        _set_task(run_id, "compose-1", status="succeeded", output_file="new-result.mp4")
        output["position"]["x"] += 50
        response = self.client.put(f"/api/canvases/{self.canvas_id}/graph", json=stale)
        self.assertEqual(response.status_code, 200, response.text)
        with self.Session() as db:
            self.assertEqual(db.get(CanvasNode, output["id"]).data["outputFile"], "new-result.mp4")
        self._patch_graph([{"op": "update_node", "node_id": output["id"], "data": {"transitions": [{"after": output["data"]["items"][0]["nodeKey"], "type": "fade"}]}}])
        with self.Session() as db:
            self.assertFalse(db.get(CanvasNode, output["id"]).data.get("outputFile"))

    def test_plan_import_is_complete_and_idempotent(self):
        first = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/to-canvas")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()["added"])
        second = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/to-canvas")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertFalse(second.json()["added"])

        with self.Session() as db:
            nodes = db.query(CanvasNode).filter_by(canvas_id=self.canvas_id).all()
            self.assertEqual(sum(node.type == "generate" for node in nodes), 2)
            self.assertEqual(sum(node.type == "prompt" for node in nodes), 2)
            output = next(node for node in nodes if node.type == "output")
            imported = db.query(AgentCanvasImport).filter_by(turn_id=self.turn_id).one()
            self.assertEqual(
                [item["nodeKey"] for item in output.data["items"]],
                [imported.node_map["shot-1"], imported.node_map["shot-2"]],
            )

    def test_fallback_auto_writes_plan_imports_canvas_and_creates_run(self):
        from app.config import settings
        with patch.object(settings, "agent_provider_configured", False), \
             patch.object(settings, "agent_fallback_rules", True), \
             patch("app.agent_executor.spawn_run") as spawn:
            created = self.client.post("/api/agent/turns", json={
                "surface": "canvas", "target_id": self.canvas_id,
                "user_input": "做一个五秒产品视频", "target_duration": 5,
                "autonomy": "auto",
            })
            self.assertEqual(created.status_code, 201, created.text)
            new_turn_id = created.json()["id"]
            run = None
            for _ in range(100):
                with self.Session() as db:
                    turn = db.get(AgentTurn, new_turn_id)
                    run = db.query(AgentRun).filter_by(turn_id=new_turn_id).one_or_none()
                    if turn and turn.plan and run:
                        break
                time.sleep(0.01)
            self.assertIsNotNone(run)
            spawn.assert_called_once_with(run.id)

        with self.Session() as db:
            turn = db.get(AgentTurn, new_turn_id)
            self.assertTrue(turn.plan)
            self.assertEqual(turn.status, "accepted")
            self.assertIsNotNone(db.query(AgentCanvasImport).filter_by(turn_id=new_turn_id).one_or_none())

    def test_accept_atomically_imports_and_binds_tasks(self):
        with patch("app.routers.agent.spawn_run") as spawn:
            response = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/accept")
        self.assertEqual(response.status_code, 200, response.text)
        run_id = response.json()["run_id"]
        self.assertTrue(run_id)
        spawn.assert_called_once_with(run_id)
        self.assertEqual(
            self.client.post(f"/api/agent/turns/{self.turn_id}/plan/accept").status_code,
            409,
        )

        with self.Session() as db:
            run = db.get(AgentRun, run_id)
            tasks = db.query(AgentTask).filter_by(run_id=run_id).all()
            imported = db.query(AgentCanvasImport).filter_by(turn_id=self.turn_id).one()
            self.assertEqual(run.canvas_id, self.canvas_id)
            self.assertEqual(run.input_snapshot["plan"]["output_node"], "compose-1")
            self.assertTrue(all(task.input_snapshot for task in tasks))
            self.assertEqual(
                {task.node_id: task.canvas_node_id for task in tasks},
                {key: imported.node_map[key] for key in ("shot-1", "shot-2", "compose-1")},
            )
            self.assertGreater(db.query(AgentRunEvent).filter_by(run_id=run_id).count(), 0)

    def test_executor_backfills_canvas_and_composed_output(self):
        with patch("app.routers.agent.spawn_run"):
            accepted = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/accept")
        run_id = accepted.json()["run_id"]
        temp = self.enterContext(tempfile.TemporaryDirectory())
        video_dir = Path(temp)

        async def fake_generation(message_id: str):
            with self.Session() as db:
                message = db.get(Message, message_id)
                message.status = "succeeded"
                message.local_video = f"{message_id}.mp4"
                db.commit()
            (video_dir / f"{message_id}.mp4").write_bytes(b"clip")

        async def fake_compose(_run_id: str, node: dict):
            self.assertEqual(node["inputs"], ["shot-1", "shot-2"])
            target = video_dir / "joined.mp4"
            target.write_bytes(b"joined")
            return target.name

        from app import agent_executor
        from app.config import settings
        with patch.object(settings, "video_dir", video_dir), \
             patch.object(settings, "agent_run_concurrency", 1), \
             patch.object(agent_executor, "run_generation", fake_generation), \
             patch.object(agent_executor, "_compose", fake_compose):
            asyncio.run(agent_executor.execute_run(run_id))

        with self.Session() as db:
            run = db.get(AgentRun, run_id)
            tasks = db.query(AgentTask).filter_by(run_id=run_id).all()
            self.assertEqual(run.status, "succeeded", run.error)
            self.assertEqual(run.output_file, "joined.mp4")
            self.assertTrue(all(task.status == "succeeded" for task in tasks))
            for task in tasks:
                canvas_node = db.get(CanvasNode, task.canvas_node_id)
                self.assertEqual(canvas_node.status, "succeeded")
            output = db.get(CanvasNode, next(t.canvas_node_id for t in tasks if t.task_type == "compose"))
            self.assertEqual(output.data["outputFile"], "joined.mp4")

        snapshot = self.client.get(f"/api/agent/runs/{run_id}")
        self.assertEqual(snapshot.status_code, 200)
        self.assertEqual(snapshot.json()["status"], "succeeded")
        self.assertIn("sig=", snapshot.json()["video_src"])

    def test_agent_patch_undo_and_human_takeover_are_versioned(self):
        imported = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/to-canvas").json()
        node_id = imported["node_map"]["shot-2"]
        context = self.client.get(f"/api/canvases/{self.canvas_id}/agent/context").json()
        payload = {
            "base_revision": context["revision"],
            "control_version": context["control_version"],
            "idempotency_key": "make-shot-two-closeup",
            "operations": [{"op": "update_node", "node_id": node_id, "data": {"inlinePrompt": "产品特写近景"}}],
        }
        changed = self.client.post(f"/api/canvases/{self.canvas_id}/agent/patch", json=payload)
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertTrue(changed.json()["changed"])
        replay = self.client.post(f"/api/canvases/{self.canvas_id}/agent/patch", json=payload)
        self.assertEqual(replay.status_code, 200)
        self.assertFalse(replay.json()["changed"])

        undone = self.client.post(f"/api/canvases/{self.canvas_id}/agent/undo")
        self.assertEqual(undone.status_code, 200, undone.text)
        restored = next(node for node in undone.json()["canvas"]["nodes"] if node["id"] == node_id)
        self.assertEqual(restored["data"]["inlinePrompt"], "产品近景")

        takeover = self.client.post(f"/api/canvases/{self.canvas_id}/takeover")
        self.assertEqual(takeover.status_code, 200)
        stale = {**payload, "base_revision": takeover.json()["revision"]}
        stale["idempotency_key"] = "stale-control-write"
        self.assertEqual(
            self.client.post(f"/api/canvases/{self.canvas_id}/agent/patch", json=stale).status_code,
            409,
        )
        with patch("app.routers.agent.spawn_run") as spawn:
            stale_accept = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/accept")
        self.assertEqual(stale_accept.status_code, 409)
        self.assertIn("人工接手", stale_accept.json()["detail"])
        spawn.assert_not_called()


    def test_accept_rejects_a_manually_changed_imported_plan(self):
        imported = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/to-canvas").json()
        context = self.client.get(f"/api/canvases/{self.canvas_id}/agent/context").json()
        changed = self.client.post(f"/api/canvases/{self.canvas_id}/agent/patch", json={
            "base_revision": context["revision"],
            "control_version": context["control_version"],
            "idempotency_key": "manual-edit-before-accept",
            "operations": [{
                "op": "update_node", "node_id": imported["node_map"]["shot-2"],
                "data": {"inlinePrompt": "用户自己改过的镜头"},
            }],
        })
        self.assertEqual(changed.status_code, 200, changed.text)

        with patch("app.routers.agent.spawn_run") as spawn:
            accepted = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/accept")
        self.assertEqual(accepted.status_code, 409, accepted.text)
        self.assertIn("手动修改", accepted.json()["detail"])
        spawn.assert_not_called()
        with self.Session() as db:
            self.assertIsNone(db.query(AgentRun).filter_by(turn_id=self.turn_id).one_or_none())

    def test_late_results_ignore_layout_but_not_changed_inputs(self):
        from app.agent_executor import _set_task

        with patch("app.routers.agent.spawn_run"):
            run_id = self.client.post(
                f"/api/agent/turns/{self.turn_id}/plan/accept",
            ).json()["run_id"]
        with self.Session() as db:
            tasks = {task.node_id: task for task in db.query(AgentTask).filter_by(run_id=run_id)}
            shot_1 = tasks["shot-1"].canvas_node_id
            shot_2 = tasks["shot-2"].canvas_node_id
        context = self.client.get(f"/api/canvases/{self.canvas_id}/agent/context").json()
        changed = self.client.post(f"/api/canvases/{self.canvas_id}/agent/patch", json={
            "base_revision": context["revision"],
            "control_version": context["control_version"],
            "idempotency_key": "layout-and-input-change-during-run",
            "operations": [
                {"op": "move_node", "node_id": shot_1, "position": {"x": 999, "y": 777}},
                {"op": "update_node", "node_id": shot_2, "data": {"inlinePrompt": "新输入"}},
            ],
        })
        self.assertEqual(changed.status_code, 200, changed.text)

        _set_task(run_id, "shot-1", status="succeeded", output_file="shot-1.mp4")
        _set_task(run_id, "shot-2", status="succeeded", output_file="stale-shot-2.mp4")
        with self.Session() as db:
            self.assertEqual(db.get(CanvasNode, shot_1).status, "succeeded")
            self.assertEqual(db.get(CanvasNode, shot_2).status, "idle")

    def test_owned_asset_id_is_resolved_for_validation_and_canvas_import(self):
        with self.Session() as db:
            asset = Asset(
                user_id=self.user_id, name="商品图", kind="image",
                filename="", source_url="https://cdn.example/product.png",
            )
            db.add(asset)
            db.flush()
            turn = db.get(AgentTurn, self.turn_id)
            plan = two_shot_plan()
            plan["nodes"][0]["capability"] = "i2v"
            plan["nodes"][0]["reference_media"] = [{
                "kind": "image", "asset_id": asset.id,
            }]
            turn.plan = plan
            db.commit()
            asset_id = asset.id

        with patch("app.routers.agent.spawn_run"):
            accepted = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/accept")
        self.assertEqual(accepted.status_code, 200, accepted.text)
        imported = self.client.get(f"/api/canvases/{self.canvas_id}").json()
        media_nodes = [node for node in imported["nodes"] if node["type"] == "image"]
        self.assertEqual(len(media_nodes), 1)
        self.assertEqual(media_nodes[0]["data"]["url"], "https://cdn.example/product.png")
        self.assertTrue(asset_id)

    def test_asset_urls_are_frozen_before_execution_including_end_frame(self):
        from app import agent_executor as executor
        from app.catalog import get_model
        from app.media_resolver import resolve_inputs

        with self.Session() as db:
            assets = [Asset(user_id=self.user_id, name=kind, kind="image", source_url=f"https://cdn.example/{kind}.png") for kind in ("first", "last")]
            db.add_all(assets)
            db.flush()
            asset_ids = [asset.id for asset in assets]
            plan = two_shot_plan()
            plan["nodes"][0].update(
                capability="i2v", reference_media=[{"kind": "image", "asset_id": asset_ids[0]}],
                end_frame={"asset_id": asset_ids[1]},
            )
            db.get(AgentTurn, self.turn_id).plan = plan
            db.commit()
        run_id = self._accept()
        with self.Session() as db:
            for asset_id in asset_ids:
                db.get(Asset, asset_id).source_url = "https://cdn.example/changed.png"
            db.commit()
        node = executor._load_task_node(run_id, "shot-1")
        message_id = executor._create_message(run_id, node)
        with self.Session() as db:
            message = db.get(Message, message_id)
            self.assertEqual([item["url"] for item in message.reference_media], ["https://cdn.example/first.png", "https://cdn.example/last.png"])
            self.assertEqual([item["kind"] for item in message.reference_media], ["image", "end_frame"])
            self.assertFalse(any("asset_id" in item for item in message.reference_media))
            model = get_model(node["model"])
            self.assertEqual(len(resolve_inputs(message.reference_media, model, model.capability("i2v"))), 2)

    def test_additional_reference_connection_rejects_old_plan(self):
        with self.Session() as db:
            plan = two_shot_plan()
            plan["nodes"][0].update(capability="r2v", reference_media=[{"kind": "image", "url": "https://cdn.example/first.png"}])
            db.get(AgentTurn, self.turn_id).plan = plan
            db.commit()
        imported = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/to-canvas").json()
        self._patch_graph([
            {"op": "add_node", "node": {"id": "extra-image", "type": "image", "position": {"x": 0, "y": 0}, "data": {"url": "https://cdn.example/extra.png"}}},
            {"op": "connect", "edge": {"id": "extra-edge", "source": "extra-image", "source_handle": "image", "target": imported["node_map"]["shot-1"], "target_handle": "image_0"}},
        ])
        with patch("app.routers.agent.spawn_run"):
            result = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/accept")
        self.assertEqual(result.status_code, 409, result.text)

    def test_reordered_aggregate_references_reject_old_plan(self):
        with self.Session() as db:
            plan = two_shot_plan()
            plan["nodes"][0].update(capability="r2v", reference_media=[
                {"kind": "image", "url": f"https://cdn.example/{index}.png"} for index in (1, 2)
            ])
            db.get(AgentTurn, self.turn_id).plan = plan
            db.commit()
        imported = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/to-canvas").json()
        graph = imported["canvas"]
        graph["edges"].reverse()
        response = self.client.put(f"/api/canvases/{self.canvas_id}/graph", json=graph)
        self.assertEqual(response.status_code, 200, response.text)
        with patch("app.routers.agent.spawn_run"):
            response = self.client.post(f"/api/agent/turns/{self.turn_id}/plan/accept")
        self.assertEqual(response.status_code, 409, response.text)

    def test_run_events_are_durable_and_replay_to_each_subscriber(self):
        from app.agent_run_service import append_run_event

        with patch("app.routers.agent.spawn_run"):
            run_id = self.client.post(
                f"/api/agent/turns/{self.turn_id}/plan/accept",
            ).json()["run_id"]
        with self.Session() as db:
            run = db.get(AgentRun, run_id)
            run.status = "failed"
            run.error = "provider failed"
            append_run_event(db, run_id, "run.status", {
                "status": "failed", "error": run.error,
            })

        bodies = [
            self.client.get(f"/api/agent/runs/{run_id}/events?from_seq=0").text
            for _ in range(2)
        ]
        for body in bodies:
            self.assertIn("event: run.status", body)
            self.assertIn("provider failed", body)
            self.assertIn("event: done", body)
            self.assertGreaterEqual(body.count("event: task.status"), 3)

    def test_cancel_stops_all_queued_tasks_before_provider_submission(self):
        from app import agent_executor

        with patch("app.routers.agent.spawn_run"):
            run_id = self.client.post(
                f"/api/agent/turns/{self.turn_id}/plan/accept",
            ).json()["run_id"]
        canceled = self.client.post(f"/api/agent/runs/{run_id}/cancel")
        self.assertEqual(canceled.status_code, 200, canceled.text)
        with patch.object(agent_executor, "run_generation") as generate:
            asyncio.run(agent_executor.execute_run(run_id))
        generate.assert_not_called()
        with self.Session() as db:
            run = db.get(AgentRun, run_id)
            tasks = db.query(AgentTask).filter_by(run_id=run_id).all()
            self.assertEqual(run.status, "canceled")
            self.assertTrue(all(task.status == "canceled" for task in tasks))

    def test_retry_only_resets_failed_and_downstream_branch(self):
        with patch("app.routers.agent.spawn_run"):
            run_id = self.client.post(
                f"/api/agent/turns/{self.turn_id}/plan/accept",
            ).json()["run_id"]
        with self.Session() as db:
            run = db.get(AgentRun, run_id)
            run.status = "failed"
            run.error = "shot-2 failed"
            tasks = {task.node_id: task for task in db.query(AgentTask).filter_by(run_id=run_id)}
            tasks["shot-1"].status = "succeeded"
            tasks["shot-1"].output_file = "keep.mp4"
            tasks["shot-2"].status = "failed"
            tasks["shot-2"].error = "provider failed"
            tasks["shot-2"].attempt_count = 1
            tasks["shot-2"].message_id = "old-provider-attempt"
            tasks["compose-1"].status = "blocked"
            tasks["compose-1"].error = "upstream failed"
            db.commit()

        with patch("app.routers.agent.spawn_run") as spawn:
            retried = self.client.post(f"/api/agent/runs/{run_id}/retry")
        self.assertEqual(retried.status_code, 200, retried.text)
        spawn.assert_called_once_with(run_id)
        with self.Session() as db:
            tasks = {task.node_id: task for task in db.query(AgentTask).filter_by(run_id=run_id)}
            self.assertEqual(tasks["shot-1"].status, "succeeded")
            self.assertEqual(tasks["shot-1"].output_file, "keep.mp4")
            self.assertEqual(tasks["shot-2"].status, "queued")
            self.assertIsNone(tasks["shot-2"].message_id)
            self.assertEqual(tasks["compose-1"].status, "queued")

    def test_cross_canvas_session_is_not_reused(self):
        with self.Session() as db:
            other = Canvas(id=uuid.uuid4().hex, user_id=self.user_id, title="另一张画布")
            db.add_all([
                other,
                Conversation(id=other.id, user_id=self.user_id, title=other.title, kind="canvas"),
            ])
            db.commit()
            other_id = other.id

        async def no_op(*_args, **_kwargs):
            return None

        with patch("app.routers.agent.orchestrate_turn", no_op):
            created = self.client.post("/api/agent/turns", json={
                "surface": "canvas", "target_id": other_id,
                "session_id": self.session_id, "user_input": "新画布的任务",
            })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertNotEqual(created.json()["session_id"], self.session_id)
        with self.Session() as db:
            session = db.get(AgentSession, created.json()["session_id"])
            self.assertEqual(session.target_id, other_id)

    def test_long_reference_fallback_keeps_media_and_parallel_clips(self):
        from app.agent_service import _fallback_plan

        with self.Session() as db:
            user = db.get(User, self.user_id)
            media = [{"kind": "image", "url": "/media/uploads/product.png"}]
            _, plan = _fallback_plan(user, "产品广告", media, 30)
        clips = [node for node in plan["nodes"] if node["type"] == "generate"]
        self.assertGreater(len(clips), 1)
        self.assertTrue(all(node["reference_media"] == media for node in clips))
        self.assertTrue(all(node["depends_on"] == [] for node in clips))

    def test_provider_tool_schema_translates_names_at_the_boundary(self):
        from app import agent_chat

        captured: dict = {}

        class FakeResponse:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def raise_for_status(self):
                return None

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"canvas__read","arguments":"{}"}}]}}]}'
                yield "data: [DONE]"

        class FakeClient:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def stream(self, _method, _url, **kwargs):
                captured.update(kwargs["json"])
                return FakeResponse()

        async def collect():
            return [item async for item in agent_chat.stream_chat(
                "agent-model", "system", [{"role": "user", "content": "read"}],
                tools=[{
                    "name": "canvas.read", "description": "read canvas",
                    "parameters": {"type": "object", "properties": {}},
                }],
            )]

        model = SimpleNamespace(
            id="provider-model", base_url="https://provider.invalid/v1", api_key="secret",
        )
        with patch.object(agent_chat, "resolve_model", return_value=model), \
             patch.object(agent_chat.httpx, "AsyncClient", FakeClient):
            events = asyncio.run(collect())
        self.assertEqual(captured["tools"][0]["function"]["name"], "canvas__read")
        self.assertEqual(events[0][0], "tool_calls")
        self.assertEqual(events[0][1][0]["function"]["name"], "canvas.read")


if __name__ == "__main__":
    unittest.main()
