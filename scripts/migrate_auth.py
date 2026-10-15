"""迁移 legacy workspace 的 auth 表到 data/auth.sqlite3"""
import sqlite3, shutil
from pathlib import Path

data_dir = Path("data")
auth_path = data_dir / "auth.sqlite3"
legacy_db = data_dir / "workspaces" / "legacy" / "workshop.sqlite3"

if not legacy_db.exists():
    print("legacy DB not found, nothing to do")
    raise SystemExit(0)

legacy = sqlite3.connect(legacy_db)
legacy.row_factory = sqlite3.Row

# 先在 auth.sqlite3 里建表
auth = sqlite3.connect(auth_path)
auth.executescript('''
CREATE TABLE IF NOT EXISTS pc_users (
  id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE,display_name TEXT NOT NULL,
  role TEXT NOT NULL,active INTEGER NOT NULL,password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pc_sessions (
  token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES pc_users(id),
  expires_at TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pc_login_failures (username TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pc_prompts (
  id TEXT PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL,current_version INTEGER NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pc_prompt_versions (
  prompt_id TEXT NOT NULL REFERENCES pc_prompts(id),version INTEGER NOT NULL,
  content TEXT NOT NULL,status TEXT NOT NULL,actor_id TEXT NOT NULL,created_at TEXT NOT NULL,
  PRIMARY KEY(prompt_id,version));
CREATE TABLE IF NOT EXISTS pc_config (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pc_batches (
  id TEXT PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL,document_ids TEXT NOT NULL,
  sampling_rate REAL NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,actor_id TEXT NOT NULL,
  prompt_snapshot TEXT,sampling_round INTEGER NOT NULL DEFAULT 0,snapshot TEXT,
  accepted_snapshot TEXT,accepted_at TEXT);
CREATE TABLE IF NOT EXISTS pc_samples (
  batch_id TEXT NOT NULL REFERENCES pc_batches(id),round INTEGER NOT NULL,chapter_id TEXT NOT NULL,
  document_id TEXT NOT NULL,revision INTEGER NOT NULL,stratum TEXT NOT NULL,
  PRIMARY KEY(batch_id,round,chapter_id));
CREATE TABLE IF NOT EXISTS pc_reviews (
  id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,round INTEGER NOT NULL,chapter_id TEXT NOT NULL,
  revision INTEGER NOT NULL,outcome TEXT NOT NULL,message TEXT NOT NULL,
  actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pc_issues (
  id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,chapter_id TEXT NOT NULL,revision INTEGER NOT NULL,
  message TEXT NOT NULL,status TEXT NOT NULL,resolution TEXT NOT NULL DEFAULT '',
  actor_id TEXT NOT NULL,created_at TEXT NOT NULL,resolved_by TEXT,resolved_at TEXT);
CREATE TABLE IF NOT EXISTS pc_audit (
  id TEXT PRIMARY KEY,actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,actor_role TEXT NOT NULL,
  event TEXT NOT NULL,detail TEXT NOT NULL,batch_id TEXT,created_at TEXT NOT NULL);
''')

# 迁移 legacy 数据
tables_to_migrate = [
    'pc_users', 'pc_sessions', 'pc_login_failures',
    'pc_prompts', 'pc_prompt_versions', 'pc_config',
    'pc_batches', 'pc_samples', 'pc_reviews', 'pc_issues', 'pc_audit',
]

for table in tables_to_migrate:
    try:
        rows = legacy.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  {table}: (empty, skip)")
            continue
        cols = [d[0] for d in legacy.execute(f"PRAGMA table_info({table})").fetchall()]
        placeholders = ','.join(['?'] * len(cols))
        col_names = ','.join(cols)
        auth.execute(f"DELETE FROM {table}")
        auth.executemany(f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})", 
                        [tuple(r[col] for col in cols) for r in rows])
        print(f"  {table}: migrated {len(rows)} rows")
    except Exception as e:
        print(f"  {table}: SKIP ({e})")

auth.commit()
legacy.close()
auth.close()
print("\n=== Migration done! ===")
