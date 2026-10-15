import sqlite3
db = sqlite3.connect('data/workshop.sqlite3')
tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print('TABLES:')
for t in tables:
    print(f'  {t[0]}')
print('\nSETTINGS:')
for r in db.execute('SELECT * FROM settings').fetchall():
    print(f'  {r}')
print('\nSCHEMA OF EACH TABLE:')
for t in tables:
    name = t[0]
    if name.startswith('_'): continue
    cols = db.execute(f'PRAGMA table_info({name})').fetchall()
    print(f'\n  {name}:')
    for c in cols:
        print(f'    {c[1]} {c[2]}')
