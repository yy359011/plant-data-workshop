import sqlite3, shutil, os
from pathlib import Path

# 直接改 legacy workspace 里的 DB
db_path = Path("data/workspaces/legacy/workshop.sqlite3")
db = sqlite3.connect(db_path)
db.row_factory = sqlite3.Row

print("=== 登录失败记录 ===")
rows = db.execute("SELECT * FROM pc_login_failures").fetchall()
for r in rows:
    print(f"  {dict(r)}")
if not rows:
    print("  （空）")

# 清空失败计数
db.execute("DELETE FROM pc_login_failures")
db.commit()
print("\n已清空登录失败记录")

# 确认 admin 用户存在
print("\n=== 用户 ===")
rows = db.execute("SELECT id, username, role, active FROM pc_users").fetchall()
for r in rows:
    print(f"  {dict(r)}")

db.close()
print("\nOK")
