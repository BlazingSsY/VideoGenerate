"""让 `pytest` 在仓库根目录直接可用（测试里用的是 `from app...`）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))
