"""v3 Agent 回归测试。

覆盖：
- 数据模型迁移（新表 / 新列）
- POST /api/agent/turns 基本流程
- GET /api/agent/turns/{id}
- GET /api/agent/sessions/{id}/messages
- POST /api/agent/turns/{id}/plan/accept
- GET /api/agent/tools
- GET /api/agent/skills
- 意图分类降级（Provider 未配置时走 fallback）
- 计划提取多级兜底
- 围栏解析器（三段式流）
- 安全约束（跨用户访问被拒）
"""
import json
import os
import sys
import unittest
from unittest.mock import patch, AsyncMock

# Ensure backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (
    AgentChatMessage, AgentRun, AgentSession, AgentStep, AgentTask,
    AgentTurn, Asset, PromptSkill, Upload, User,
)
from app.routers import agent, conversations
from app.security import current_user
from app.agent_chat import _extract_plan, FenceParser


class AgentV3TestBase(unittest.TestCase):
    """Shared test fixtures."""

    def setUp(self):
        from app.config import settings
        self.original_fallback = settings.agent_fallback_rules
        self.original_configured = settings.agent_provider_configured
        settings.agent_fallback_rules = True
        settings.agent_provider_configured = False  # force fallback mode

        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        with self.Session() as db:
            admin = User(username="admin_user", password_hash="x", role="admin")
            regular = User(username="regular_user", password_hash="x", role="user")
            other = User(username="other_user", password_hash="x", role="user")
            db.add_all([admin, regular, other])
            db.commit()
            db.refresh(admin); db.refresh(regular); db.refresh(other)
            self.admin_id = admin.id
            self.user_id = regular.id
            self.other_user_id = other.id

        app = FastAPI()
        app.include_router(agent.router)
        app.include_router(conversations.router)

        def db_override():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        def admin_override():
            with self.Session() as db:
                return db.get(User, self.admin_id)

        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[current_user] = admin_override
        # with-scope：持久 portal，让 create_task 的后台任务存活于共享 loop
        # （无 with 时每个请求独立 portal，请求结束 loop 销毁，后台任务被丢弃）
        self.client = self.enterContext(TestClient(app))

    def tearDown(self):
        from app.config import settings
        settings.agent_fallback_rules = self.original_fallback
        settings.agent_provider_configured = self.original_configured
        self.client.close()
        self.engine.dispose()


class TestDatabaseMigration(AgentV3TestBase):
    """验证新表和新列存在。"""

    def test_agent_chat_messages_table_exists(self):
        with self.Session() as db:
            msg = AgentChatMessage(
                session_id="test-session", role="user", content="hello",
            )
            db.add(msg)
            db.commit()
            self.assertIsNotNone(msg.id)

    def test_agent_steps_table_exists(self):
        with self.Session() as db:
            step = AgentStep(
                turn_id="test-turn", seq=1, kind="classify",
                title="test", payload={}, status="done",
            )
            db.add(step)
            db.commit()
            self.assertIsNotNone(step.id)

    def test_assets_table_exists(self):
        with self.Session() as db:
            asset = Asset(
                user_id=self.admin_id, name="test.png", kind="image",
                category="test", filename="test.png",
            )
            db.add(asset)
            db.commit()
            self.assertIsNotNone(asset.id)

    def test_agent_turn_has_v3_columns(self):
        with self.Session() as db:
            from datetime import datetime, timezone, timedelta
            session = AgentSession(user_id=self.admin_id, surface="studio")
            db.add(session); db.flush()
            turn = AgentTurn(
                session_id=session.id, user_input="test",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                intent="generate", tool_call_count=2, reasoning_tokens=100,
            )
            db.add(turn); db.commit(); db.refresh(turn)
            self.assertEqual(turn.intent, "generate")
            self.assertEqual(turn.tool_call_count, 2)
            self.assertEqual(turn.reasoning_tokens, 100)

    def test_prompt_skill_has_v3_columns(self):
        with self.Session() as db:
            skill = PromptSkill(
                name="test_skill_v3", description="test",
                instructions="test instructions", enabled=True,
                requires={"capability": "t2v"}, inputs=[{"name": "style"}],
                plan_shape="single", max_nodes=1,
            )
            db.add(skill); db.commit(); db.refresh(skill)
            self.assertEqual(skill.plan_shape, "single")
            self.assertEqual(skill.max_nodes, 1)
            self.assertEqual(skill.requires, {"capability": "t2v"})


class TestAgentToolsEndpoint(AgentV3TestBase):

    def test_tools_endpoint_returns_registry(self):
        response = self.client.get("/api/agent/tools")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        tool_names = [t["name"] for t in data]
        self.assertIn("library.search", tool_names)
        self.assertIn("history.search", tool_names)
        self.assertIn("url.fetch", tool_names)
        self.assertIn("catalog.describe", tool_names)

    def test_skills_endpoint_returns_v3_fields(self):
        with self.Session() as db:
            skill = PromptSkill(
                name="v3_skill_test", description="v3 test",
                instructions="instructions", enabled=True,
                requires={"capability": "r2v"}, inputs=[],
                plan_shape="fanout", max_nodes=6,
            )
            db.add(skill); db.commit()
        response = self.client.get("/api/agent/skills")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        found = [s for s in data if s["name"] == "v3_skill_test"]
        self.assertTrue(found)
        self.assertEqual(found[0]["plan_shape"], "fanout")
        self.assertEqual(found[0]["max_nodes"], 6)
        self.assertEqual(found[0]["requires"], {"capability": "r2v"})


class TestPlanExtraction(unittest.TestCase):
    """测试 _extract_plan 的多级兜底。"""

    def test_plan_fence(self):
        content = '说明文字\n\n```plan\n{"title":"test","nodes":[],"output_node":"","reason":""}\n```'
        plan = _extract_plan(content)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["title"], "test")

    def test_json_fence(self):
        content = '```json\n{"title":"test","nodes":[{"id":"a","type":"generate"}],"output_node":"a","reason":""}\n```'
        plan = _extract_plan(content)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["title"], "test")

    def test_bare_fence_with_json(self):
        content = '说明\n\n```\n{"title":"test","nodes":[],"output_node":"","reason":""}\n```'
        plan = _extract_plan(content)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["title"], "test")

    def test_bare_json_in_text(self):
        content = '好的，计划如下：{"title":"test","nodes":[],"output_node":"","reason":""}'
        plan = _extract_plan(content)
        self.assertIsNotNone(plan)

    def test_no_plan_returns_none(self):
        content = "这是一个普通的回答，没有计划。"
        plan = _extract_plan(content)
        self.assertIsNone(plan)

    def test_user_code_block_does_not_break(self):
        content = '用户说的 ``` 不是计划。\n\n```plan\n{"title":"real","nodes":[],"output_node":"","reason":""}\n```'
        plan = _extract_plan(content)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["title"], "real")


class TestFenceParser(unittest.TestCase):
    """测试三段式围栏状态机。"""

    def test_simple_text(self):
        parser = FenceParser()
        text, plan = parser.feed("Hello world")
        self.assertIn("Hello", text)
        self.assertIsNone(plan)

    def test_plan_fence_in_one_chunk(self):
        parser = FenceParser()
        raw = '说明文字\n\n```plan\n{"title":"test","nodes":[],"output_node":"","reason":""}\n```'
        text, plan = parser.feed(raw)
        self.assertIn("说明文字", text)
        self.assertIsNotNone(plan)
        self.assertIn("test", plan)

    def test_plan_fence_across_chunks(self):
        parser = FenceParser()
        # First chunk: text + start of fence
        text1, plan1 = parser.feed("正文说明\n\n```plan\n")
        self.assertIn("正文说明", text1)
        self.assertIsNone(plan1)
        # Second chunk: JSON content + closing fence
        text2, plan2 = parser.feed('{"title":"x","nodes":[],"output_node":"","reason":""}\n```')
        # Plan should be extracted on this feed
        self.assertIsNotNone(plan2)
        self.assertIn("title", plan2)

    def test_text_after_plan_fence(self):
        parser = FenceParser()
        raw = '```plan\n{"title":"t","nodes":[],"output_node":"","reason":""}\n```\n后续文字'
        text, plan = parser.feed(raw)
        self.assertIsNotNone(plan)
        # Flush remaining buffer at end of stream
        text2, _ = parser.flush()
        full_text = text + text2
        self.assertIn("后续文字", full_text)


class TestAgentTurnsEndpoint(AgentV3TestBase):
    """测试 POST /api/agent/turns 和相关端点。"""

    def test_create_turn_fallback_mode(self):
        """Provider 未配置时走 fallback 规则规划。"""
        response = self.client.post("/api/agent/turns", json={
            "surface": "studio",
            "user_input": "做一个15秒的日落视频",
            "autonomy": "ask",
        })
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertIn("id", data)
        self.assertIn("session_id", data)
        self.assertEqual(data["status"], "draft")

    def test_create_turn_with_reference_media(self):
        """带上参考媒体也能创建 turn。"""
        response = self.client.post("/api/agent/turns", json={
            "surface": "studio",
            "user_input": "让图动起来",
            "autonomy": "ask",
            "reference_media": [
                {"kind": "image", "url": "https://example.com/test.jpg", "name": "test"},
            ],
        })
        self.assertEqual(response.status_code, 201)

    def test_get_turn_detail(self):
        """创建 turn 后可以查询详情。"""
        create = self.client.post("/api/agent/turns", json={
            "surface": "studio", "user_input": "测试视频", "autonomy": "ask",
        })
        turn_id = create.json()["id"]
        response = self.client.get(f"/api/agent/turns/{turn_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], turn_id)
        self.assertEqual(data["user_input"], "测试视频")
        self.assertIn("steps", data)

    def test_get_turn_not_found(self):
        response = self.client.get("/api/agent/turns/nonexistent")
        self.assertEqual(response.status_code, 404)

    def test_session_messages_endpoint(self):
        """创建 turn 后 session 有对话消息。"""
        # 先用同一 session 创建两个 turn
        create1 = self.client.post("/api/agent/turns", json={
            "surface": "studio", "user_input": "第一个", "autonomy": "ask",
        })
        session_id = create1.json()["session_id"]
        # 查询 session messages
        response = self.client.get(f"/api/agent/sessions/{session_id}/messages")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    def test_accept_turn_plan_empty_plan_rejected(self):
        """没有计划的 turn 不能 accept。"""
        create = self.client.post("/api/agent/turns", json={
            "surface": "studio", "user_input": "测试", "autonomy": "ask",
        })
        turn_id = create.json()["id"]
        response = self.client.post(f"/api/agent/turns/{turn_id}/plan/accept")
        # Should be 400 because plan is empty at this point
        # (fallback may or may not have populated it depending on async timing)
        self.assertIn(response.status_code, [400, 409])

    def test_cross_user_access_denied(self):
        """用户不能访问其他用户的 turn。"""
        # Create as admin
        create = self.client.post("/api/agent/turns", json={
            "surface": "studio", "user_input": "admin的", "autonomy": "ask",
        })
        turn_id = create.json()["id"]

        # Switch to other user
        from app.config import settings
        app = FastAPI()
        app.include_router(agent.router)

        def db_override():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        def other_user_override():
            with self.Session() as db:
                return db.get(User, self.other_user_id)

        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[current_user] = other_user_override
        other_client = TestClient(app)

        response = other_client.get(f"/api/agent/turns/{turn_id}")
        self.assertEqual(response.status_code, 404)

        other_client.close()

    def test_agent_disabled_returns_503(self):
        """AGENT_ENABLED=false 时返回 503。"""
        from app.config import settings
        original = settings.agent_enabled
        settings.agent_enabled = False
        try:
            response = self.client.post("/api/agent/turns", json={
                "surface": "studio", "user_input": "test", "autonomy": "ask",
            })
            self.assertEqual(response.status_code, 503)
        finally:
            settings.agent_enabled = original


class TestAgentToolsModule(unittest.TestCase):
    """直接测试工具层函数。"""

    def test_catalog_describe_all(self):
        from app.agent_tools import catalog_describe
        result = catalog_describe()
        self.assertIn("models", result)
        self.assertTrue(len(result["models"]) > 0)

    def test_catalog_describe_single(self):
        from app.agent_tools import catalog_describe
        result = catalog_describe("happyhorse-1.1-t2v")
        self.assertEqual(result["id"], "happyhorse-1.1-t2v")
        self.assertIn("resolutions", result)

    def test_catalog_describe_unknown(self):
        from app.agent_tools import catalog_describe
        result = catalog_describe("nonexistent-model")
        self.assertIn("error", result)

    def test_extract_urls(self):
        from app.agent_tools import extract_urls_from_input
        urls = extract_urls_from_input("看看这个 https://example.com/img.jpg 和 http://test.com/page")
        self.assertEqual(len(urls), 2)

    def test_extract_urls_empty(self):
        from app.agent_tools import extract_urls_from_input
        urls = extract_urls_from_input("没有链接的文字")
        self.assertEqual(len(urls), 0)

    def test_url_fetch_rejects_non_whitelist(self):
        from app.agent_tools import url_fetch
        result = url_fetch("https://evil.com", ["https://safe.com"])
        self.assertFalse(result["ok"])

    def test_url_fetch_rejects_private_ip(self):
        from app.agent_tools import url_fetch
        # 127.0.0.1 is private but also needs to be in whitelist
        result = url_fetch("http://127.0.0.1", ["http://127.0.0.1"])
        self.assertFalse(result["ok"])


class TestExistingAgentCompat(AgentV3TestBase):
    """v1.5 兼容端点收敛后：/models 保留，/plans 与 /runs 已删除（v3 对话链路取代）。"""

    def test_get_models_still_works(self):
        response = self.client.get("/api/agent/models")
        self.assertEqual(response.status_code, 200)

    def test_old_plans_endpoints_removed(self):
        self.assertEqual(
            self.client.post("/api/agent/plans", json={"surface": "studio", "user_input": "x"}).status_code, 404)
        self.assertEqual(self.client.get("/api/agent/runs/nonexistent").status_code, 404)


class TestSSEEventStream(AgentV3TestBase):
    """阶段 3：SSE 事件序号、去重与 fallback 全程可订阅。

    orchestrate_turn 在后台 task 里直连 SessionLocal，测试须把
    agent_chat / agent_executor 模块内的 SessionLocal 指到隔离库，
    否则后台任务写真实数据库后失败，SSE 永远等不到 done。
    """

    def setUp(self):
        super().setUp()
        from app.config import settings
        import app.agent_chat as chat_mod
        import app.routers.agent as router_mod
        import app.agent_executor as executor_mod
        self._patches = [
            patch.object(chat_mod, "SessionLocal", self.Session),
            patch.object(router_mod, "SessionLocal", self.Session),
            patch.object(executor_mod, "SessionLocal", self.Session),
        ]
        for p in self._patches:
            p.start()
        self._orig_idle = settings.agent_stream_idle_timeout
        settings.agent_stream_idle_timeout = 2

    def tearDown(self):
        from app.config import settings
        settings.agent_stream_idle_timeout = self._orig_idle
        for p in self._patches:
            p.stop()
        super().tearDown()

    def test_push_event_assigns_monotonic_seq(self):
        from app.agent_chat import _push_event, get_event_queue, cleanup_event_queue

        cleanup_event_queue("turn-test")
        _push_event("turn-test", {"event": "ping", "data": {}})
        _push_event("turn-test", {"event": "text.delta", "data": {"text": "a"}})
        _push_event("turn-test", {"event": "text.delta", "data": {"text": "b"}})

        q = get_event_queue("turn-test")
        events = [q.get_nowait() for _ in range(3)]
        seqs = [e["data"]["seq"] for e in events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), 3)
        # 显式带 seq 的事件不被覆盖
        _push_event("turn-test", {"event": "x", "data": {"seq": 99}})
        self.assertEqual(q.get_nowait()["data"]["seq"], 99)
        cleanup_event_queue("turn-test")

    def test_sse_replay_then_queue_without_duplicates(self):
        """重连场景：replay 已落库 step + queue 中 from_seq 之后的事件，旧 seq 被跳过。"""
        from datetime import datetime, timezone, timedelta
        from app.agent_chat import _save_step, _push_event, cleanup_event_queue
        from app.models import AgentSession, AgentTurn

        # 直接在隔离库建 session+turn，不经 POST /turns（避免后台 orchestrate 干扰 queue）
        with self.Session() as db:
            session = AgentSession(user_id=self.admin_id, surface="studio", target_id="")
            db.add(session); db.flush()
            turn = AgentTurn(
                session_id=session.id, user_input="sse测试", expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
            )
            db.add(turn); db.commit()
            turn_id = turn.id

        with self.Session() as db:
            _save_step(db, turn_id, 3, "text", "回答", {"content": "重放内容"})
        _push_event(turn_id, {"event": "text.delta", "data": {"seq": 2, "text": "老数据"}})
        _push_event(turn_id, {"event": "text.delta", "data": {"seq": 4, "text": "新数据"}})
        # 终止事件：让 SSE generator 在 server 侧自行结束（否则 TestClient 挂到 idle timeout）
        _push_event(turn_id, {"event": "done", "data": {"seq": 5, "turn_id": turn_id}})

        # from_seq=2 时：DB 中 seq>2 的 step 走重放（step.replay），
        # queue 中 seq=2 被过滤，seq=4 放行
        chunks = []
        with self.client.stream(
            "GET", f"/api/agent/turns/{turn_id}/events", params={"from_seq": 2},
        ) as response:
            self.assertEqual(response.status_code, 200)
            for chunk in response.iter_text():
                chunks.append(chunk)
                body_so_far = "".join(chunks)
                if "新数据" in body_so_far and "step.replay" in body_so_far:
                    break
        body = "".join(chunks)
        self.assertIn("step.replay", body)
        self.assertIn("重放内容", body)
        self.assertNotIn("老数据", body)
        self.assertIn("新数据", body)
        cleanup_event_queue(turn_id)

    def test_fallback_turn_stream_ends_with_done(self):
        """fallback 模式整轮：SSE 流推进到 done 事件（loading 能复位的前提）。"""
        create = self.client.post("/api/agent/turns", json={
            "surface": "studio", "user_input": "做一个5秒的海浪视频", "autonomy": "ask",
        })
        turn_id = create.json()["id"]
        import time
        from app.agent_chat import cleanup_event_queue
        deadline = time.time() + 10
        got_done = False
        while time.time() < deadline:
            with self.client.stream(
                "GET", f"/api/agent/turns/{turn_id}/events", params={"from_seq": 0},
            ) as response:
                body = ""
                for chunk in response.iter_text():
                    body += chunk
                    if "event: done" in body or "event: error" in body:
                        break
            if "event: done" in body or "event: error" in body:
                got_done = True
                break
            time.sleep(0.2)
        self.assertTrue(got_done, f"stream never terminated: {body[:500]}")
        # fallback 出计划 → plan.draft 带 turn_id（前端 PlanCard 执行按钮依赖）
        self.assertIn("plan.draft", body)
        self.assertIn(turn_id, body)
        cleanup_event_queue(turn_id)


class TestFenceParserFlush(unittest.TestCase):
    """parser.feed("") 旧用法改为 flush() 后的边界行为。"""

    def test_flush_emits_trailing_text(self):
        parser = FenceParser()
        text, _ = parser.feed("结尾三个字")
        text2, plan = parser.flush()
        self.assertEqual(text + text2, "结尾三个字")
        self.assertIsNone(plan)

    def test_flush_unclosed_fence_yields_plan(self):
        parser = FenceParser()
        _, _ = parser.feed('```plan\n{"title":"x","nodes":[],"output_node":"","reason":""}')
        _, plan = parser.flush()
        self.assertIsNotNone(plan)
        self.assertIn("title", plan)


if __name__ == "__main__":
    unittest.main()


class TestAgentP1Features(AgentV3TestBase):
    """阶段 4：工具调用循环 / token 记账与限额 / 降级链路。"""

    def setUp(self):
        super().setUp()
        from app.config import settings
        import app.agent_chat as chat_mod
        import app.routers.agent as router_mod
        import app.agent_executor as executor_mod
        self._patches = [
            patch.object(chat_mod, "SessionLocal", self.Session),
            patch.object(router_mod, "SessionLocal", self.Session),
            patch.object(executor_mod, "SessionLocal", self.Session),
        ]
        for p in self._patches:
            p.start()
        self._orig_idle = settings.agent_stream_idle_timeout
        settings.agent_stream_idle_timeout = 2

    def tearDown(self):
        from app.config import settings as s
        s.agent_stream_idle_timeout = self._orig_idle
        for p in self._patches:
            p.stop()
        super().tearDown()

    def _stream_turn(self, user_input: str):
        import time
        from app.agent_chat import cleanup_event_queue
        create = self.client.post("/api/agent/turns", json={
            "surface": "studio", "user_input": user_input, "autonomy": "ask",
        })
        turn_id = create.json()["id"]
        deadline = time.time() + 10
        body = ""
        while time.time() < deadline:
            with self.client.stream(
                "GET", f"/api/agent/turns/{turn_id}/events", params={"from_seq": 0},
            ) as response:
                body = ""
                for chunk in response.iter_text():
                    body += chunk
                    if "event: done" in body or "event: error" in body:
                        break
            if "event: done" in body or "event: error" in body:
                break
            time.sleep(0.2)
        return turn_id, body

    def test_tool_call_loop_emits_events_and_feeds_back(self):
        """monkeypatch stream_chat 伪 tool_calls → 断言 tool.call/tool.result 事件与 messages 回喂。

        TestClient 的 portal loop 在请求间隙不调度 create_task 的后台任务，
        必须靠轮询普通 JSON 端点让 loop tick 推进 orchestrate；
        轮到 answered 后一次性 SSE replay 断言事件流。
        """
        import time
        from app.agent_chat import cleanup_event_queue
        from app.models import User as UserModel
        cfg = __import__("app.config", fromlist=["settings"]).settings
        self._orig_cfg = cfg.agent_provider_configured
        cfg.agent_provider_configured = True
        self.addCleanup(setattr, cfg, "agent_provider_configured", self._orig_cfg)
        import app.agent_chat as chat_mod
        calls = {"rounds": 0, "messages_seen": []}

        async def fake_stream_chat(model_id, system_prompt, messages, tools=None):
            calls["rounds"] += 1
            calls["messages_seen"].append(messages)
            calls["tools"] = tools
            if calls["rounds"] == 1:
                yield ("think", "思考")
                yield ("tool_calls", [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "library.search", "arguments": '{"query": "海浪"}'},
                }])
                yield ("usage", {"prompt_tokens": 30, "completion_tokens": 5})
            else:
                yield ("text", '出片：```plan\n{"title":"海浪片","target_duration":5,"nodes":[{"id":"shot-1","type":"generate","prompt":"海浪","model":"wan3.0-video-prime","capability":"t2v","resolution":"1080P","ratio":"adaptive","duration":5,"reference_media":[],"end_frame":null,"depends_on":[],"reason":"r"}],"output_node":"shot-1","reason":"r"}\n```')
                yield ("usage", {"prompt_tokens": 60, "completion_tokens": 20})

        # 预置素材，让 library.search 有命中
        with self.Session() as db:
            db.add(Asset(user_id=self.admin_id, name="海浪照片", kind="image",
                         category="natural", filename="a.png", description="海边"))
            db.commit()

        with patch.object(chat_mod, "stream_chat", fake_stream_chat):
            create = self.client.post("/api/agent/turns", json={
                "surface": "studio", "user_input": "做一个海浪视频", "autonomy": "ask",
            })
            turn_id = create.json()["id"]
            # 轮询 GET /turns/{id}（JSON 端点让 loop tick），等 orchestrate 完成
            deadline = time.time() + 15
            while time.time() < deadline:
                detail = self.client.get(f"/api/agent/turns/{turn_id}").json()
                if detail.get("status") == "answered" and detail.get("tool_call_count", 0) > 0:
                    break
                time.sleep(0.3)
            # SSE 一次性 replay（终止事件已由 cleanup 前的 queue 保留；读够即断）
            chunks = []
            with self.client.stream(
                "GET", f"/api/agent/turns/{turn_id}/events", params={"from_seq": 0},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                for chunk in resp.iter_text():
                    chunks.append(chunk)
                    if "event: done" in "".join(chunks) or "event: error" in "".join(chunks):
                        break
            body = "".join(chunks)
        cleanup_event_queue(turn_id)

        self.assertIn("event: tool.call", body)
        self.assertIn("library.search", body)
        self.assertIn("event: tool.result", body)
        self.assertGreaterEqual(calls["rounds"], 2)
        second = calls["messages_seen"][1]
        self.assertTrue(any(m.get("tool_calls") for m in second))
        self.assertTrue(any(m.get("role") == "tool" for m in second))
        # tools 参数确实下发了注册表
        self.assertIsNotNone(calls.get("tools"))
        # token 记账落 turn
        with self.Session() as db:
            turn = db.get(AgentTurn, turn_id)
            self.assertGreater(turn.tokens_in, 0)
            self.assertGreater(turn.tokens_out, 0)
            self.assertGreaterEqual(turn.tool_call_count, 1)

    def test_daily_token_limit_returns_429(self):
        """当日累计超限 → create_turn 429。"""
        from datetime import datetime, timezone, timedelta
        from app.config import settings
        with self.Session() as db:
            session = AgentSession(user_id=self.admin_id, surface="studio", target_id="")
            db.add(session); db.flush()
            big = AgentTurn(
                session_id=session.id, user_input="x",
                tokens_in=settings.agent_daily_token_limit,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
            )
            db.add(big); db.commit()

        create = self.client.post("/api/agent/turns", json={
            "surface": "studio", "user_input": "再来一个", "autonomy": "ask",
        })
        self.assertEqual(create.status_code, 429)
        self.assertIn("上限", create.json()["detail"])

    def test_unconfigured_provider_no_crash_chat_fallback(self):
        """provider 未配置时 chat 意图整轮不崩（S1 规则分类 + S4 固定话术）。"""
        turn_id, body = self._stream_turn("wan 和 minimax 有什么区别？怎么选")
        self.assertIn("event: done", body)
        self.assertIn("当前未连接智能体服务", body)
        with self.Session() as db:
            turn = db.get(AgentTurn, turn_id)
            self.assertEqual(turn.status, "answered")


class TestAssetsLibrary(AgentV3TestBase):
    """素材库完整版：上传落库补录、外链登记、检索、越权保护。"""

    def setUp(self):
        super().setUp()
        from app.routers import uploads as uploads_router
        # uploads 路由挂在独立 app 上（AgentV3TestBase 只 include agent/conversations）
        self.app = FastAPI()
        from app.routers import assets
        self.app.include_router(uploads_router.router)
        self.app.include_router(assets.router)
        self.app.dependency_overrides[get_db] = self.client.app.dependency_overrides[get_db]
        # current_user override 需要能切换用户：默认 admin，其他用户用 with_user
        self._current_user_impl = None
        self.app.dependency_overrides[current_user] = self._switchable_current_user
        self.lib = self.enterContext(TestClient(self.app))

    def _switchable_current_user(self):
        from app.models import User as UserModel
        uid = self._current_user_impl or self.admin_id
        with self.Session() as db:
            return db.get(UserModel, uid)

    def as_user(self, user_id):
        self._current_user_impl = user_id
        return self

    def test_upload_creates_asset_record(self):
        """上传文件 → 同事务写 Upload + Asset（素材自动入库）。"""
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        from app.config import settings
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            orig = settings.upload_dir
            settings.upload_dir = Path(tmp)
            try:
                r = self.lib.post("/api/uploads", files={"file": ("海浪.png", png, "image/png")})
                self.assertEqual(r.status_code, 200)
                url = r.json()["url"]
                r2 = self.lib.get("/api/assets")
                self.assertEqual(r2.status_code, 200)
                rows = r2.json()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["name"], "海浪")
                self.assertEqual(rows[0]["kind"], "image")
                self.assertEqual(rows[0]["file_url"], url)
                self.assertIn("sig=", rows[0]["preview_url"])
            finally:
                settings.upload_dir = orig

    def test_external_link_registration(self):
        """外链补录：source_url 登记，不落文件。"""
        r = self.lib.post("/api/assets", json={
            "name": "海浪视频", "kind": "video", "category": "natural",
            "description": "冲浪",
            "source_url": "https://cdn.example.com/wave.mp4",
        })
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["source_url"], "https://cdn.example.com/wave.mp4")
        self.assertEqual(body["file_url"], "https://cdn.example.com/wave.mp4")

        # 搜索命中
        r2 = self.lib.get("/api/assets", params={"q": "海浪"})
        self.assertEqual(len(r2.json()), 1)
        # kind 过滤
        r3 = self.lib.get("/api/assets", params={"kind": "image"})
        self.assertEqual(len(r3.json()), 0)

    def test_patch_and_delete(self):
        self.lib.post("/api/assets", json={
            "name": "测试", "kind": "image", "source_url": "https://x.example.com/a.png"})
        asset_id = self.lib.get("/api/assets").json()[0]["id"]
        r = self.lib.patch(f"/api/assets/{asset_id}", json={"name": "改名后", "category": "cat"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "改名后")

        # 越权：其他用户不可见/不可删
        self.as_user(self.other_user_id)
        r404 = self.lib.delete(f"/api/assets/{asset_id}")
        self.assertEqual(r404.status_code, 404)
        r403list = self.lib.get("/api/assets")
        self.assertEqual(len(r403list.json()), 0)

        # 归属用户删除
        self.as_user(self.admin_id)
        r204 = self.lib.delete(f"/api/assets/{asset_id}")
        self.assertEqual(r204.status_code, 204)

    def test_invalid_file_url_rejected(self):
        """file_url 指向不存在的上传 → 404；目录穿越 → 400。"""
        r1 = self.lib.post("/api/assets", json={
            "name": "x", "kind": "image", "file_url": "/media/uploads/nonexistent.png"})
        self.assertEqual(r1.status_code, 404)
        r2 = self.lib.post("/api/assets", json={
            "name": "x", "kind": "image", "file_url": "/media/uploads/../secret.png"})
        self.assertEqual(r2.status_code, 400)

    def test_library_search_tool_includes_url(self):
        """library.search 工具结果带 url（agent 检索/拖拽用）。"""
        from app.agent_tools import library_search
        with self.Session() as db:
            db.add(Asset(user_id=self.admin_id, name="海浪照片", kind="image",
                         category="natural", filename="a.png", description="海边"))
            db.add(Asset(user_id=self.admin_id, name="冲浪", kind="video",
                         source_url="https://cdn.example.com/w.mp4"))
            db.commit()
        rows = library_search(db, self.admin_id, query="海浪")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "/media/uploads/a.png")
        rows2 = library_search(db, self.admin_id, kind="video")
        self.assertEqual(rows2[0]["url"], "https://cdn.example.com/w.mp4")


class TestUrlFetchSsrf(AgentV3TestBase):
    """url.fetch 的 SSRF 与 DNS rebinding 防护（规划 §17 验收）。"""

    def test_private_host_rejected(self):
        from app.agent_tools import url_fetch
        for url in (
            "http://127.0.0.1/x",
            "http://localhost/x",
            "http://10.1.2.3/x",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/x",
            "http://192.168.1.1/admin",
        ):
            result = url_fetch(url, whitelist=[url])
            self.assertFalse(result.get("ok"), f"{url} 应被拒绝")
            self.assertIn("内网", result.get("error", ""))

    def test_non_whitelisted_url_rejected(self):
        from app.agent_tools import url_fetch
        result = url_fetch("https://example.com/a", whitelist=["https://other.example/b"])
        self.assertFalse(result.get("ok"))

    def test_pinning_survives_dns_change(self):
        """校验后 DNS 改答（rebinding）：建连使用钉扎 IP，而非二次解析。"""
        import httpcore
        from unittest.mock import patch
        from app import agent_tools

        # 1) _pinned_transport 的网络后端被整体替换为 _PinnedBackend（持有钉扎表）
        transport = agent_tools._pinned_transport({"example.com": "93.184.216.34"})
        self.assertIsInstance(transport._pool._network_backend, agent_tools._PinnedBackend)

        # 2) 已校验的 host：建连目标 = 钉扎 IP（拦住父类 connect_tcp，不真正联网）
        captured = {}

        def spy(self, host, port, timeout=None, local_address=None, socket_options=None):
            captured["connect_host"] = host
            raise _StopConnect

        class _StopConnect(Exception):
            pass

        backend = agent_tools._PinnedBackend({"example.com": "93.184.216.34"})
        with patch.object(httpcore.SyncBackend, "connect_tcp", spy):
            try:
                backend.connect_tcp("example.com", 443)
            except _StopConnect:
                pass
        self.assertEqual(captured.get("connect_host"), "93.184.216.34",
                         "建连目标必须是钉扎 IP")

        # 3) 未校验的 host 在进入 socket 前被拒绝
        with patch.object(httpcore.SyncBackend, "connect_tcp", spy):
            try:
                backend.connect_tcp("unvalidated.host", 443)
                self.fail("未校验 host 不应建连")
            except _StopConnect:
                self.fail("未校验 host 不应到达 socket 层")
            except Exception as exc:
                self.assertIn("不在已校验列表", str(exc))


class TestExecutorRunTask(AgentV3TestBase):
    """阶段 6 §17：executor 执行链路向 turn 的 SSE 队列推送 run.task 事件。"""

    def setUp(self):
        super().setUp()
        import app.agent_chat as chat_mod
        import app.routers.agent as router_mod
        import app.agent_executor as executor_mod
        self._executor = executor_mod
        self._patches = [
            patch.object(chat_mod, "SessionLocal", self.Session),
            patch.object(router_mod, "SessionLocal", self.Session),
            patch.object(executor_mod, "SessionLocal", self.Session),
        ]
        for p in self._patches:
            p.start()
        self._turn_ids = []

    def tearDown(self):
        from app.agent_chat import cleanup_event_queue
        for turn_id in self._turn_ids:
            cleanup_event_queue(turn_id)
        for p in self._patches:
            p.stop()
        super().tearDown()

    def _create_run(self, nodes, output_node=""):
        """直连隔离库建 session/turn/run/tasks，返回 (run_id, turn_id)。"""
        from datetime import datetime, timezone, timedelta
        with self.Session() as db:
            session = AgentSession(user_id=self.user_id, surface="studio", target_id="")
            db.add(session)
            db.flush()
            turn = AgentTurn(
                session_id=session.id,
                user_input="executor 测试",
                status="answered",
                plan={"title": "测试计划", "nodes": nodes, "output_node": output_node},
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            db.add(turn)
            db.flush()
            run = AgentRun(turn_id=turn.id, user_id=self.user_id, status="queued")
            db.add(run)
            db.flush()
            for node in nodes:
                db.add(AgentTask(
                    run_id=run.id,
                    node_id=node["id"],
                    task_type=node.get("type", "generate"),
                    status="queued",
                    depends_on=node.get("depends_on", []),
                ))
            db.commit()
            self._turn_ids.append(turn.id)
            return run.id, turn.id

    def _fake_run_generation(self):
        """按 resolved_prompt 是否含『必败』决定 Message 结局，绕开真实生成。"""
        from app.models import Message

        async def runner(message_id):
            with self.Session() as db:
                message = db.get(Message, message_id)
                if message is None:
                    raise RuntimeError("消息不存在")
                if "必败" in message.resolved_prompt:
                    message.status = "failed"
                    message.error = "生成服务不可用"
                else:
                    message.status = "succeeded"
                    message.local_video = f"agent-{message.id}.mp4"
                db.commit()

        return runner

    def _drain(self, turn_id):
        import asyncio
        from app.agent_chat import get_event_queue
        queue = get_event_queue(turn_id)
        out = []
        while True:
            try:
                out.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                return out

    def test_success_pushes_run_task_lifecycle(self):
        nodes = [{
            "id": "n1", "type": "generate", "model": "happyhorse",
            "capability": "t2v", "prompt": "一只猫在打字", "duration": 5,
        }]
        run_id, turn_id = self._create_run(nodes, output_node="n1")

        import asyncio
        with patch.object(self._executor, "run_generation", self._fake_run_generation()):
            asyncio.run(self._executor.execute_run(run_id))

        events = [e for e in self._drain(turn_id) if e["event"] == "run.task"]
        lifecycle = [(e["data"]["node_id"], e["data"]["status"]) for e in events]
        self.assertEqual(lifecycle, [
            ("n1", "queued"), ("n1", "running"), ("n1", "succeeded"), ("__run__", "succeeded"),
        ])

        node_done = next(e for e in events
                         if e["data"]["node_id"] == "n1" and e["data"]["status"] == "succeeded")
        self.assertTrue(node_done["data"]["video_src"].startswith("/media/videos/"))
        self.assertIn("sig=", node_done["data"]["video_src"])

        run_done = next(e for e in events if e["data"]["node_id"] == "__run__")
        self.assertEqual(run_done["data"]["status"], "succeeded")
        self.assertIn("sig=", run_done["data"]["video_src"])

        with self.Session() as db:
            run = db.get(AgentRun, run_id)
            task = db.query(AgentTask).filter_by(run_id=run_id).one()
            turn = db.get(AgentTurn, turn_id)
            self.assertEqual(run.status, "succeeded")
            self.assertTrue(run.output_file.startswith("agent-"))
            self.assertEqual(task.status, "succeeded")
            self.assertEqual(task.output_file, run.output_file)
            self.assertIsNotNone(task.message_id)
            self.assertEqual(turn.status, "executed")

    def test_failure_pushes_failed_event_and_fails_run(self):
        nodes = [{
            "id": "n1", "type": "generate", "model": "happyhorse",
            "capability": "t2v", "prompt": "必败场景", "duration": 5,
        }]
        run_id, turn_id = self._create_run(nodes, output_node="n1")

        import asyncio
        with patch.object(self._executor, "run_generation", self._fake_run_generation()):
            asyncio.run(self._executor.execute_run(run_id))

        events = [e for e in self._drain(turn_id) if e["event"] == "run.task"]
        lifecycle = [(e["data"]["node_id"], e["data"]["status"]) for e in events]
        self.assertEqual(lifecycle, [("n1", "queued"), ("n1", "running"), ("n1", "failed")])
        self.assertIn("生成服务不可用", events[-1]["data"]["error"])

        with self.Session() as db:
            run = db.get(AgentRun, run_id)
            task = db.query(AgentTask).filter_by(run_id=run_id).one()
            turn = db.get(AgentTurn, turn_id)
            self.assertEqual(run.status, "failed")
            self.assertIn("n1", run.error)
            self.assertEqual(task.status, "failed")
            self.assertEqual(task.error, "生成服务不可用")
            # 失败路径不把 turn 误标为 executed
            self.assertEqual(turn.status, "answered")

    def test_compose_waits_for_dependencies(self):
        """双生成 + 合成：依赖全部就绪后才执行合成节点。"""
        from pathlib import Path
        import tempfile
        from app.config import settings
        from app.models import Message
        import app.agent_executor as ex
        import asyncio

        nodes = [
            {"id": "g1", "type": "generate", "model": "happyhorse", "capability": "t2v",
             "prompt": "片段一", "duration": 5},
            {"id": "g2", "type": "generate", "model": "happyhorse", "capability": "t2v",
             "prompt": "片段二", "duration": 5},
            {"id": "join", "type": "compose", "inputs": ["g1", "g2"], "depends_on": ["g1", "g2"]},
        ]
        run_id, turn_id = self._create_run(nodes, output_node="join")

        # 视频目录指到临时目录，避免污染真实 media 目录
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patched_dir = Path(tmp.name)
        dir_patch = patch.object(settings, "video_dir", patched_dir, create=True)
        dir_patch.start()
        self.addCleanup(dir_patch.stop)

        async def fake_gen(message_id):
            with self.Session() as db:
                message = db.get(Message, message_id)
                message.status = "succeeded"
                message.local_video = f"agent-{message.id}.mp4"
                db.commit()
            (patched_dir / f"agent-{message_id}.mp4").write_bytes(b"dummy")

        compose_inputs = {}

        async def fake_compose(rid, node):
            compose_inputs["order"] = list(node["inputs"])
            for node_id in node["inputs"]:
                path = ex._task_output(rid, str(node_id))
                assert path.is_file(), f"合成输入 {node_id} 必须已存在于磁盘"
            return "joined-join.mp4"

        with patch.object(ex, "run_generation", fake_gen), \
             patch.object(ex, "_compose", fake_compose):
            asyncio.run(ex.execute_run(run_id))

        events = [e for e in self._drain(turn_id) if e["event"] == "run.task"]
        lifecycle = [(e["data"]["node_id"], e["data"]["status"]) for e in events]
        self.assertEqual(lifecycle, [
            ("g1", "queued"), ("g2", "queued"), ("join", "queued"),
            ("g1", "running"), ("g1", "succeeded"),
            ("g2", "running"), ("g2", "succeeded"),
            ("join", "running"), ("join", "succeeded"),
            ("__run__", "succeeded"),
        ])
        self.assertEqual(compose_inputs["order"], ["g1", "g2"])

        with self.Session() as db:
            run = db.get(AgentRun, run_id)
            self.assertEqual(run.status, "succeeded")
            self.assertEqual(run.output_file, "joined-join.mp4")
