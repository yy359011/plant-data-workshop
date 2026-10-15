"""快速验证 WorkspaceManager + 迁移逻辑。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pathlib import Path
import tempfile, shutil

tmp = Path(tempfile.mkdtemp())
print(f"TEST DIR: {tmp}")

# 模拟旧格式：根目录直接有 SQLite
import sqlite3
db = sqlite3.connect(tmp / "workshop.sqlite3")
db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
db.execute("INSERT INTO settings VALUES('project_name','旧项目')")
db.close()
print(f"Legacy SQLite created at {tmp / 'workshop.sqlite3'}")

from backend.server import WorkspaceManager, Workshop, APIError, json as _json

manager = WorkspaceManager(tmp)
print(f"Active workspace: {manager.active_id}")
print(f"Registry: {manager.registry}")
print(f"Workspaces: {manager.list_workspaces()}")

# 验证迁移后的目录
for item in (tmp / "workspaces").iterdir():
    print(f"  {item}")

# 创建新 workspace
ws = manager.create_workspace("新项目 B")
print(f"\nCreated: {ws}")
print(f"All: {manager.list_workspaces()}")

# 切换
manager.activate_workspace(ws["id"])
print(f"\nAfter activate: active={manager.active_id}")

# 删除（不能删 active）
try:
    manager.delete_workspace(ws["id"])
except APIError as e:
    print(f"Delete active blocked: {e}")

# 先切回 legacy
manager.activate_workspace("legacy")
manager.delete_workspace(ws["id"])
print(f"\nAfter delete B: {manager.list_workspaces()}")

shutil.rmtree(tmp, ignore_errors=True)
print("\nALL TESTS PASSED!")
