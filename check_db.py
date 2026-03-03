"""Quick DB diagnostic — run with: python check_db.py"""
from database import get_db_connection, _USE_POSTGRES

print("PostgreSQL:", _USE_POSTGRES)
conn = get_db_connection()
cur = conn.cursor()
cur.execute("SELECT COUNT(*) as total FROM monitored_accounts")
row = cur.fetchone()
print("Total accounts:", row["total"] if isinstance(row, dict) else row[0])
conn.close()
