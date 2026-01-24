import sqlite3

conn = sqlite3.connect('backend_core/data/kumar_core.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cursor.fetchall()]
print("Database Tables:")
for t in tables:
    print(f"  - {t}")
print(f"\nTotal: {len(tables)} tables")
conn.close()
