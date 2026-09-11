import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import CanvasNode, Conversation, Message, User
from app.routers import canvas, conversations, media
from app.security import current_user


class CanvasApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        with self.Session() as db:
            user = User(username="canvas-user", password_hash="test", role="user")
            admin = User(username="canvas-admin", password_hash="test", role="admin")
            db.add(user)
            db.add(admin)
            db.commit()
            db.refresh(user)
            db.refresh(admin)
            self.user_id = user.id
            self.admin_id = admin.id
            self.current_user_id = user.id

        app = FastAPI()
        app.include_router(canvas.router)
        app.include_router(conversations.router)
        app.include_router(media.router)

        def db_override():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        def user_override():
            with self.Session() as db:
                return db.get(User, self.current_user_id)

        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[current_user] = user_override
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def test_create_canvas_builds_hidden_shadow_conversation(self):
        response = self.client.post("/api/canvases", json={"title": "分镜草案"})

        self.assertEqual(response.status_code, 201)
        canvas_id = response.json()["id"]
        with self.Session() as db:
            shadow = db.get(Conversation, canvas_id)
            self.assertIsNotNone(shadow)
            self.assertEqual(shadow.kind, "canvas")

        conversations_response = self.client.get("/api/conversations")
        self.assertEqual(conversations_response.status_code, 200)
        self.assertEqual(conversations_response.json(), [])

    def test_admin_cannot_open_another_users_canvas(self):
        canvas_id = self.client.post("/api/canvases", json={}).json()["id"]
        self.current_user_id = self.admin_id

        response = self.client.get(f"/api/canvases/{canvas_id}")

        self.assertEqual(response.status_code, 404)

    def test_admin_cannot_download_another_users_canvas_result(self):
        canvas_id = self.client.post("/api/canvases", json={}).json()["id"]
        with self.Session() as db:
            result = Message(
                conversation_id=canvas_id,
                role="assistant",
                status="succeeded",
                video_url="https://example.com/private.mp4",
            )
            db.add(result)
            db.commit()
            db.refresh(result)
            message_id = result.id
        self.current_user_id = self.admin_id

        response = self.client.get(f"/api/messages/{message_id}/download")

        self.assertEqual(response.status_code, 404)

    def test_graph_save_uses_optimistic_lock(self):
        created = self.client.post("/api/canvases", json={}).json()
        graph = {
            "updated_at": created["updated_at"],
            "viewport": {"x": 12, "y": 18, "zoom": 0.9},
            "nodes": [
                {
                    "id": "prompt-1",
                    "type": "prompt",
                    "position": {"x": 0, "y": 0},
                    "data": {"text": "雨夜街道"},
                }
            ],
            "edges": [],
        }

        saved = self.client.put(f"/api/canvases/{created['id']}/graph", json=graph)
        self.assertEqual(saved.status_code, 200)
        stale = self.client.put(f"/api/canvases/{created['id']}/graph", json=graph)
        self.assertEqual(stale.status_code, 409)

    def test_run_generate_node_uses_connected_values_without_context_rewrite(self):
        created = self.client.post("/api/canvases", json={}).json()
        graph = {
            "updated_at": created["updated_at"],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "nodes": [
                {
                    "id": "prompt-1",
                    "type": "prompt",
                    "position": {"x": 0, "y": 0},
                    "data": {"text": "红色旗袍女性，低角度仰拍"},
                },
                {
                    "id": "image-1",
                    "type": "image",
                    "position": {"x": 0, "y": 180},
                    "data": {"url": "https://example.com/reference.jpg", "name": "参考图"},
                },
                {
                    "id": "generate-1",
                    "type": "generate",
                    "position": {"x": 400, "y": 0},
                    "data": {
                        "model": "happyhorse-1.1-r2v",
                        "capability": "r2v",
                        "resolution": "1080P",
                        "ratio": "16:9",
                        "duration": 5,
                        "watermark": True,
                        "audio": None,
                        "inlinePrompt": "这段内联提示词不应被使用",
                    },
                },
            ],
            "edges": [
                {
                    "id": "edge-prompt",
                    "source": "prompt-1",
                    "source_handle": "out",
                    "target": "generate-1",
                    "target_handle": "prompt",
                },
                {
                    "id": "edge-image",
                    "source": "image-1",
                    "source_handle": "out",
                    "target": "generate-1",
                    "target_handle": "image_0",
                },
            ],
        }
        saved = self.client.put(f"/api/canvases/{created['id']}/graph", json=graph)
        self.assertEqual(saved.status_code, 200)

        with patch("app.routers.canvas.spawn") as spawn:
            response = self.client.post(
                f"/api/canvases/{created['id']}/nodes/generate-1/run"
            )

        self.assertEqual(response.status_code, 202)
        message_id = response.json()["message_id"]
        spawn.assert_called_once_with(message_id)
        with self.Session() as db:
            message = db.get(Message, message_id)
            node = db.get(CanvasNode, "generate-1")
            self.assertEqual(message.resolved_prompt, "红色旗袍女性，低角度仰拍")
            self.assertEqual(message.reference_images, ["https://example.com/reference.jpg"])
            self.assertFalse(message.params["use_context"])
            self.assertEqual(node.message_id, message_id)
            self.assertEqual(node.status, "pending")

    def test_run_generate_node_collects_video_audio_and_exposes_output_node(self):
        self.current_user_id = self.admin_id
        created = self.client.post("/api/canvases", json={}).json()
        graph = {
            "updated_at": created["updated_at"],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "nodes": [
                {
                    "id": "prompt-1",
                    "type": "prompt",
                    "position": {"x": 0, "y": 0},
                    "data": {"text": "让视频 1 中的人物跟随音频 1 说话"},
                },
                {
                    "id": "video-1",
                    "type": "video",
                    "position": {"x": 0, "y": 180},
                    "data": {"url": "https://example.com/reference.mp4", "name": "动作参考"},
                },
                {
                    "id": "audio-1",
                    "type": "audio",
                    "position": {"x": 0, "y": 360},
                    "data": {"url": "https://example.com/voice.mp3", "name": "台词参考"},
                },
                {
                    "id": "generate-1",
                    "type": "generate",
                    "position": {"x": 420, "y": 0},
                    "data": {
                        "model": "wan3.0-video-prime",
                        "capability": "r2v",
                        "resolution": "1080P",
                        "ratio": "adaptive",
                        "duration": 5,
                        "watermark": False,
                        "audio": True,
                        "inlinePrompt": "",
                    },
                },
                {
                    "id": "output-1",
                    "type": "output",
                    "position": {"x": 840, "y": 0},
                    "data": {},
                },
            ],
            "edges": [
                {
                    "id": "edge-prompt",
                    "source": "prompt-1",
                    "source_handle": "out",
                    "target": "generate-1",
                    "target_handle": "prompt",
                },
                {
                    "id": "edge-video",
                    "source": "video-1",
                    "source_handle": "out",
                    "target": "generate-1",
                    "target_handle": "video_0",
                },
                {
                    "id": "edge-audio",
                    "source": "audio-1",
                    "source_handle": "out",
                    "target": "generate-1",
                    "target_handle": "audio_0",
                },
                {
                    "id": "edge-output",
                    "source": "generate-1",
                    "source_handle": "out",
                    "target": "output-1",
                    "target_handle": "in",
                },
            ],
        }
        saved = self.client.put(f"/api/canvases/{created['id']}/graph", json=graph)
        self.assertEqual(saved.status_code, 200)

        with patch("app.routers.canvas.spawn") as spawn:
            response = self.client.post(
                f"/api/canvases/{created['id']}/nodes/generate-1/run"
            )

        self.assertEqual(response.status_code, 202)
        spawn.assert_called_once()
        with self.Session() as db:
            message = db.get(Message, response.json()["message_id"])
            self.assertEqual(
                message.reference_media,
                [
                    {
                        "kind": "video",
                        "url": "https://example.com/reference.mp4",
                        "name": "动作参考",
                    },
                    {
                        "kind": "audio",
                        "url": "https://example.com/voice.mp3",
                        "name": "台词参考",
                    },
                ],
            )
            self.assertEqual(message.reference_images, [])


if __name__ == "__main__":
    unittest.main()
