import sqlite3, hashlib
from pathlib import Path

db_path = Path("data/workspaces/legacy/workshop.sqlite3")
db = sqlite3.connect(db_path)
db.row_factory = sqlite3.Row

print("=== admin user ===")
row = db.execute("SELECT * FROM pc_users WHERE username='admin'").fetchone()
print(f"  id={row['id']} role={row['role']} active={row['active']}")
print(f"  hash prefix: {row['password_hash'][:30]}...")

# 用明文算一下 admin123456 的 hash 对照
import bcrypt
try:
    h = bcrypt.hashpw(b"admin123456", bcrypt.gensalt(rounds=4))
    print(f"  fresh hash: {h.decode()[:30]}...")
    match = bcrypt.checkpw(b"admin123456", row['password_hash'].encode() if isinstance(row['password_hash'], str) else row['password_hash'])
    print(f"  match? {match}")
except Exception as e:
    print(f"  bcrypt error: {e}")
    # 也可能是 pbkdf2
    stored = row['password_hash']
    print(f"  stored type: {type(stored)}")
    print(f"  stored repr: {stored!r}")

db.close()
