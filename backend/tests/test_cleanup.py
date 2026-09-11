"""视频保留期清理。"""
import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from test_security import RELOAD_ORDER, reload_app_modules


def _fresh(retention="7"):
    tmp = tempfile.mkdtemp()
    os.environ.update(
        {
            "DATA_DIR": tmp,
            "SECRET_KEY": "x" * 40,
            "VIDEO_RETENTION_DAYS": retention,
            "DASHSCOPE_API_KEY_WAN": "k",
            "DASHSCOPE_API_KEY_HAPPYHORSE": "k",
        }
    )
    os.environ.pop("FRONTEND_DIST", None)
    reload_app_modules()
    import app.cleanup

    importlib.reload(app.cleanup)
    return app.cleanup, Path(tmp)


def _naive_utc_days_ago(days):
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


class VideoRetentionTests(unittest.TestCase):
    def setUp(self):
        self.cleanup, self.tmp = _fresh()
        from app.database import Base, SessionLocal, engine
        from app.models import Conversation, Message, User

        Base.metadata.create_all(bind=engine)
        self.SessionLocal = SessionLocal
        self.Message = Message
        db = SessionLocal()
        user = User(id="u1", username="u", password_hash="x", role="admin")
        db.add(user)
        db.add(Conversation(id="c1", user_id="u1", title="t"))
        db.commit()
        db.close()
        self.videos = self.tmp / "videos"

    def _add(self, message_id, days_ago, size=1024):
        """建一条已完成的生成记录，并写出对应的视频文件。"""
        path = self.videos / f"{message_id}.mp4"
        path.write_bytes(b"0" * size)
        db = self.SessionLocal()
        db.add(
            self.Message(
                id=message_id,
                conversation_id="c1",
                role="assistant",
                status="succeeded",
                local_video=path.name,
                created_at=_naive_utc_days_ago(days_ago),
            )
        )
        db.commit()
        db.close()
        return path

    def test_deletes_videos_past_retention(self):
        old = self._add("old1", days_ago=8)
        fresh = self._add("new1", days_ago=2)

        result = self.cleanup.sweep()

        self.assertFalse(old.exists(), "超过 7 天的视频应被删除")
        self.assertTrue(fresh.exists(), "未到保留期的视频必须保留")
        self.assertEqual(result["expired"], 1)

    def test_keeps_record_and_marks_expired(self):
        self._add("old2", days_ago=10)
        self.cleanup.sweep()

        db = self.SessionLocal()
        row = db.get(self.Message, "old2")
        # 记录不能被删——对话历史、提示词、参数都要留着
        self.assertIsNotNone(row)
        self.assertEqual(row.status, "succeeded")
        self.assertEqual(row.local_video, "")
        self.assertTrue(row.video_expired)
        db.close()

    def test_boundary_just_under_retention(self):
        almost = self._add("edge", days_ago=6.9)
        self.cleanup.sweep()
        self.assertTrue(almost.exists(), "刚好不到 7 天不应被删")

    def test_removes_orphan_files(self):
        """删对话只删了数据行，文件会留在磁盘上，孤儿扫描要能兜住。"""
        orphan = self.videos / "no-such-record.mp4"
        orphan.write_bytes(b"0" * 2048)
        old_mtime = os.path.getmtime(orphan) - self.cleanup.ORPHAN_GRACE_SECONDS - 60
        os.utime(orphan, (old_mtime, old_mtime))

        result = self.cleanup.sweep()

        self.assertFalse(orphan.exists())
        self.assertEqual(result["orphans"], 1)

    def test_keeps_recent_orphan_within_grace(self):
        """刚落盘、事务还没提交的文件不能被误删。"""
        just_written = self.videos / "in-flight.mp4"
        just_written.write_bytes(b"0" * 512)
        self.cleanup.sweep()
        self.assertTrue(just_written.exists())

    def test_cleans_stale_part_files(self):
        part = self.videos / "abandoned.mp4.part"
        part.write_bytes(b"0" * 4096)
        old_mtime = os.path.getmtime(part) - self.cleanup.ORPHAN_GRACE_SECONDS - 60
        os.utime(part, (old_mtime, old_mtime))

        self.cleanup.sweep()
        self.assertFalse(part.exists(), "中断的下载残留应被清理")

    def test_reports_freed_bytes(self):
        self._add("big", days_ago=9, size=5000)
        result = self.cleanup.sweep()
        self.assertEqual(result["freed_bytes"], 5000)

    def test_retention_disabled(self):
        cleanup, tmp = _fresh(retention="0")
        from app.database import Base, engine

        Base.metadata.create_all(bind=engine)
        stale = tmp / "videos" / "keep-me.mp4"
        stale.write_bytes(b"0" * 128)
        # 关闭保留期后不做过期清理；这个文件没有记录，但在宽限期内也不该被当孤儿删掉
        result = cleanup.sweep()
        self.assertEqual(result["expired"], 0)
        self.assertTrue(stale.exists())


class DeleteCascadeTests(unittest.TestCase):
    def test_delete_videos_for_removes_files(self):
        cleanup, tmp = _fresh()
        from app.database import Base, SessionLocal, engine
        from app.models import Conversation, Message, User

        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        db.add(User(id="u1", username="u", password_hash="x", role="admin"))
        db.add(Conversation(id="c1", user_id="u1", title="t"))
        path = tmp / "videos" / "gone.mp4"
        path.write_bytes(b"0" * 777)
        db.add(
            Message(
                id="m1",
                conversation_id="c1",
                role="assistant",
                status="succeeded",
                local_video="gone.mp4",
            )
        )
        db.commit()

        conversation = db.get(Conversation, "c1")
        freed = cleanup.delete_videos_for(conversation.messages)
        db.close()

        self.assertEqual(freed, 777)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
