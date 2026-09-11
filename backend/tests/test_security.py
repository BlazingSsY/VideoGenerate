"""覆盖 review 中修掉的几个问题，防止回归。"""
import importlib
import sys
import io
import os
import tempfile
import unittest
from pathlib import Path


RELOAD_ORDER = (
    "app.config",
    "app.database",
    "app.models",
    "app.security",
    "app.ratelimit",
    "app.catalog",
    "app.media_links",
    "app.media_resolver",
    "app.dashscope",
    "app.prompt_context",
    "app.prompt_skills",
    "app.tasks",
    "app.agent_executor",
    "app.cleanup",
    "app.canvas_graph",
    "app.routers.conversations",
    "app.canvas_executor",
    "app.routers.auth",
    "app.routers.users",
    "app.routers.skills",
    "app.routers.catalog",
    "app.routers.uploads",
    "app.routers.canvas",
    "app.routers.media",
    "app.main",
)


def reload_app_modules():
    """按依赖顺序重载整套 app 模块。

    settings 是模块级单例，各模块用 `from ..config import settings` 捕获了引用，
    改环境变量后必须整体重载，否则拿到的还是旧配置。

    注意不能写成 `reload(import_module(name))`——模块若尚未导入过，
    import_module 会先执行一遍、reload 再执行一遍，SQLAlchemy 的表会重复定义。
    """
    for name in RELOAD_ORDER:
        module = sys.modules.get(name)
        if module is None:
            importlib.import_module(name)
        else:
            importlib.reload(module)


def _fresh_app(**env):
    """用独立的临时目录和环境变量重新加载一套 app（配置在 import 期读取）。"""
    tmp = tempfile.mkdtemp()
    dist = Path(tmp) / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (Path(tmp) / "SECRET_OUTSIDE.txt").write_text("TOP-SECRET-CONTENT", encoding="utf-8")

    os.environ.update(
        {
            "DATA_DIR": tmp,
            "FRONTEND_DIST": str(dist),
            "SECRET_KEY": "x" * 40,
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "admin123",
            "DASHSCOPE_API_KEY_WAN": "k",
            "DASHSCOPE_API_KEY_HAPPYHORSE": "k",
            **env,
        }
    )

    reload_app_modules()
    import app.main

    return app.main.app, tmp


class PathTraversalTests(unittest.TestCase):
    """SPA 兜底路由曾经可以读到 dist 目录之外的任意文件。"""

    def test_spa_route_cannot_escape_dist(self):
        from fastapi.testclient import TestClient

        application, _ = _fresh_app()
        with TestClient(application) as client:
            for attack in (
                "/../SECRET_OUTSIDE.txt",
                "/..%2fSECRET_OUTSIDE.txt",
                "/%2e%2e/SECRET_OUTSIDE.txt",
                "/a/../../SECRET_OUTSIDE.txt",
                "/%2e%2e%2f%2e%2e%2f.env",
            ):
                response = client.get(attack)
                self.assertNotIn("TOP-SECRET-CONTENT", response.text, attack)

    def test_spa_still_serves_real_assets(self):
        from fastapi.testclient import TestClient

        application, tmp = _fresh_app()
        (Path(tmp) / "dist" / "robots.txt").write_text("ok", encoding="utf-8")
        with TestClient(application) as client:
            self.assertEqual(client.get("/robots.txt").text, "ok")
            # 未知路由仍然回落到 index.html，前端路由才能工作
            self.assertIn("app", client.get("/canvas").text)


class UploadTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        application, _ = _fresh_app(MAX_UPLOAD_MB="1")
        self.ctx = TestClient(application)
        self.client = self.ctx.__enter__()
        token = self.client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        ).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def _upload(self, name, payload):
        return self.client.post(
            "/api/uploads",
            files={"file": (name, io.BytesIO(payload), "image/png")},
            headers=self.headers,
        )

    def test_rejects_oversize(self):
        response = self._upload("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024))
        self.assertEqual(response.status_code, 413)

    def test_rejects_html_disguised_as_png(self):
        response = self._upload("evil.png", b"<html><script>alert(1)</script></html>")
        self.assertEqual(response.status_code, 400)

    def test_rejects_extension_content_mismatch(self):
        response = self._upload("shot.png", b"\xff\xd8\xff\xdb" + b"0" * 64)
        self.assertEqual(response.status_code, 400)

    def test_accepts_real_png(self):
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        )
        response = self._upload("ok.png", png)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["url"].startswith("/media/uploads/"))

    def test_accepts_video_and_audio_media(self):
        mp4 = self._upload("reference.mp4", b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
        mp3 = self._upload("voice.mp3", b"ID3\x04\x00\x00" + b"0" * 64)

        self.assertEqual(mp4.status_code, 200)
        self.assertEqual(mp4.json()["kind"], "video")
        self.assertEqual(mp3.status_code, 200)
        self.assertEqual(mp3.json()["kind"], "audio")


class LoginThrottleTests(unittest.TestCase):
    def test_locks_out_after_repeated_failures(self):
        from fastapi.testclient import TestClient

        application, _ = _fresh_app(LOGIN_MAX_ATTEMPTS="3", LOGIN_LOCKOUT_SECONDS="60")
        with TestClient(application) as client:
            codes = [
                client.post(
                    "/api/auth/login", json={"username": "admin", "password": "nope"}
                ).status_code
                for _ in range(5)
            ]
            self.assertEqual(codes[:3], [401, 401, 401])
            self.assertEqual(codes[3:], [429, 429])
            # 锁定期间即使密码正确也拒绝
            blocked = client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin123"}
            )
            self.assertEqual(blocked.status_code, 429)

    def test_successful_login_clears_counter(self):
        from fastapi.testclient import TestClient

        application, _ = _fresh_app(LOGIN_MAX_ATTEMPTS="5", LOGIN_LOCKOUT_SECONDS="60")
        with TestClient(application) as client:
            for _ in range(3):
                client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
            ok = client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin123"}
            )
            self.assertEqual(ok.status_code, 200)
            for _ in range(4):
                bad = client.post(
                    "/api/auth/login", json={"username": "admin", "password": "nope"}
                )
            self.assertEqual(bad.status_code, 401)


class SecurityHeaderTests(unittest.TestCase):
    def test_nosniff_present(self):
        from fastapi.testclient import TestClient

        application, _ = _fresh_app()
        with TestClient(application) as client:
            headers = client.get("/api/health").headers
            self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(headers.get("X-Frame-Options"), "SAMEORIGIN")


class SignedMediaTests(unittest.TestCase):
    """/media 下的文件必须凭签名链接访问，且签名不可挪用、会过期。"""

    def setUp(self):
        from fastapi.testclient import TestClient

        application, tmp = _fresh_app()
        self.tmp = Path(tmp)
        (self.tmp / "videos").mkdir(exist_ok=True)
        (self.tmp / "videos" / "demo.mp4").write_bytes(b"fake-video-bytes")
        self.ctx = TestClient(application)
        self.client = self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def _sign(self, path, ttl=None):
        from app.media_links import sign_path

        return sign_path(path, ttl)

    def test_requires_signature(self):
        self.assertEqual(self.client.get("/media/videos/demo.mp4").status_code, 403)

    def test_valid_signature_works_without_login(self):
        # <video> 标签带不了 Authorization 头，所以签名链接必须免登录可用
        response = self.client.get(self._sign("/media/videos/demo.mp4"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"fake-video-bytes")

    def test_signed_video_supports_byte_ranges(self):
        response = self.client.get(self._sign("/media/videos/demo.mp4"), headers={"Range": "bytes=0-4"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"fake-")
        self.assertEqual(response.headers.get("accept-ranges"), "bytes")
        self.assertEqual(response.headers.get("content-range"), "bytes 0-4/16")
        self.assertEqual(response.headers.get("content-length"), "5")

    def test_signed_video_supports_suffix_ranges(self):
        response = self.client.get(self._sign("/media/videos/demo.mp4"), headers={"Range": "bytes=-5"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"bytes")

    def test_signed_video_can_be_switched_to_streaming_download(self):
        signed = self._sign("/media/videos/demo.mp4")
        separator = "&" if "?" in signed else "?"

        response = self.client.get(f"{signed}{separator}download=1")

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers.get("content-disposition", ""))
        self.assertEqual(response.content, b"fake-video-bytes")

    def test_signature_is_bound_to_path(self):
        signed = self._sign("/media/videos/demo.mp4")
        query = signed.split("?", 1)[1]
        moved = self.client.get(f"/media/videos/other.mp4?{query}")
        self.assertEqual(moved.status_code, 403)

    def test_expired_signature_rejected(self):
        expired = self._sign("/media/videos/demo.mp4", ttl=-10)
        response = self.client.get(expired)
        self.assertEqual(response.status_code, 403)
        self.assertIn("过期", response.json()["detail"])

    def test_tampered_signature_rejected(self):
        response = self.client.get("/media/videos/demo.mp4?exp=9999999999&sig=" + "0" * 32)
        self.assertEqual(response.status_code, 403)

    def test_traversal_blocked_even_with_signature(self):
        signed = self._sign("/media/videos/demo.mp4")
        query = signed.split("?", 1)[1]
        response = self.client.get(f"/media/videos/..%2f..%2f.env?{query}")
        self.assertIn(response.status_code, (403, 404))


class PromptSkillTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        application, _ = _fresh_app()
        self.ctx = TestClient(application)
        self.client = self.ctx.__enter__()
        token = self.client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        ).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_admin_can_install_and_list_a_prompt_skill(self):
        created = self.client.post(
            "/api/skills",
            headers=self.headers,
            json={
                "name": "电影感增强",
                "description": "增强镜头、光影与氛围",
                "instructions": "补充景别、运镜、光影和声音设计，只输出最终提示词。",
                "enabled": True,
            },
        )

        self.assertEqual(created.status_code, 201)
        listed = self.client.get("/api/skills", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertIn("电影感增强", [item["name"] for item in listed.json()])

    def test_regular_user_cannot_install_a_prompt_skill(self):
        user = self.client.post(
            "/api/users",
            headers=self.headers,
            json={"username": "skill-user", "password": "123456", "role": "user"},
        )
        self.assertEqual(user.status_code, 201)
        token = self.client.post(
            "/api/auth/login", json={"username": "skill-user", "password": "123456"}
        ).json()["access_token"]

        response = self.client.post(
            "/api/skills",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "越权技能", "instructions": "不应被安装"},
        )

        self.assertEqual(response.status_code, 403)

    def _install(self, *, enabled=True):
        response = self.client.post(
            "/api/skills",
            headers=self.headers,
            json={
                "name": "镜头增强",
                "description": "补充镜头语言",
                "instructions": "补充景别和运镜，只输出最终提示词。",
                "enabled": enabled,
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def _conversation(self):
        response = self.client.post(
            "/api/conversations", headers=self.headers, json={"model": "happyhorse-1.1-t2v"}
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def test_text_to_video_records_selected_skill(self):
        skill = self._install()
        conversation_id = self._conversation()

        response = self.client.post(
            f"/api/conversations/{conversation_id}/generate",
            headers=self.headers,
            json={
                "prompt": "雨夜中的未来城市",
                "model": "happyhorse-1.1-t2v",
                "capability": "t2v",
                "resolution": "1080P",
                "ratio": "16:9",
                "duration": 5,
                "skill_id": skill["id"],
            },
        )

        self.assertEqual(response.status_code, 200)
        params = response.json()["user_message"]["params"]
        self.assertEqual(params["skill_id"], skill["id"])
        self.assertEqual(params["skill_name"], "镜头增强")

    def test_image_to_video_rejects_skill(self):
        skill = self._install()
        conversation_id = self._conversation()

        response = self.client.post(
            f"/api/conversations/{conversation_id}/generate",
            headers=self.headers,
            json={
                "prompt": "让画面动起来",
                "model": "happyhorse-1.1-i2v",
                "capability": "i2v",
                "resolution": "1080P",
                "ratio": "",
                "duration": 5,
                "reference_images": ["https://example.com/frame.png"],
                "skill_id": skill["id"],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("仅可用于文生视频", response.json()["detail"])

    def test_unknown_skill_is_rejected(self):
        conversation_id = self._conversation()
        response = self.client.post(
            f"/api/conversations/{conversation_id}/generate",
            headers=self.headers,
            json={
                "prompt": "雨夜中的未来城市",
                "model": "happyhorse-1.1-t2v",
                "capability": "t2v",
                "resolution": "1080P",
                "ratio": "16:9",
                "duration": 5,
                "skill_id": "missing-skill",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("不存在", response.json()["detail"])


class PromptSkillEnhancementTests(unittest.IsolatedAsyncioTestCase):
    async def test_enhancement_failure_falls_back_to_original_prompt(self):
        from unittest.mock import AsyncMock, patch

        _fresh_app()
        from app.prompt_skills import enhance_prompt

        with patch("app.prompt_skills.chat", AsyncMock(side_effect=RuntimeError("offline"))):
            result = await enhance_prompt("原始提示词", "增加电影感", "test-key")

        self.assertEqual(result, "原始提示词")


class DomainConfigTests(unittest.TestCase):
    def test_app_domain_derives_public_url_and_hosts(self):
        import importlib

        os.environ["APP_DOMAIN"] = "video.example.com"
        os.environ.pop("PUBLIC_BASE_URL", None)
        os.environ.pop("ALLOWED_ORIGINS", None)
        os.environ.pop("TRUSTED_HOSTS", None)
        import app.config

        importlib.reload(app.config)
        settings = app.config.settings
        self.assertEqual(settings.public_base_url, "https://video.example.com")
        self.assertEqual(settings.allowed_origins, ["https://video.example.com"])
        self.assertIn("video.example.com", settings.trusted_hosts)
        os.environ.pop("APP_DOMAIN")
        importlib.reload(app.config)

    def test_public_base_url_host_is_trusted(self):
        """域名走 Caddy、回源用公网 IP 时，阿里云取图的 Host 是 IP，必须放行。"""
        import importlib

        os.environ["APP_DOMAIN"] = "video.example.com"
        os.environ["PUBLIC_BASE_URL"] = "http://47.98.123.45:8008"
        os.environ.pop("TRUSTED_HOSTS", None)
        import app.config

        importlib.reload(app.config)
        self.assertIn("47.98.123.45", app.config.settings.trusted_hosts)
        self.assertIn("video.example.com", app.config.settings.trusted_hosts)
        os.environ.pop("APP_DOMAIN")
        os.environ.pop("PUBLIC_BASE_URL")
        importlib.reload(app.config)


if __name__ == "__main__":
    unittest.main()
