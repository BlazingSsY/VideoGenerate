"""让 `pytest` 在仓库根目录直接可用（测试里用的是 `from app...`）。"""
import sys
import os
import tempfile
from pathlib import Path

# Some endpoint tests schedule background workers outside their DB override.
# Keep their default connection and media directories away from the real app.db.
_test_data = tempfile.TemporaryDirectory(prefix="vg-pytest-")
os.environ["DATA_DIR"] = _test_data.name
# An empty override prevents .env from supplying a production URL while letting
# tests which replace DATA_DIR keep their own independent databases.
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))


def pytest_sessionstart(session):
    from app.database import Base, engine
    from app import models  # register tables for background worker fallbacks
    Base.metadata.create_all(engine)
    session.config.add_cleanup(engine.dispose)
