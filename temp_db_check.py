from backend_core.app import db
from sqlalchemy import text

engine = db.engine
with engine.connect() as conn:
    res = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
    print('tables:', [r[0] for r in res.fetchall()])
    try:
        r2 = conn.execute(text("SELECT count(*) FROM users"))
        print('user_count:', r2.fetchone()[0])
    except Exception as e:
        print('user_count_error:', e)
