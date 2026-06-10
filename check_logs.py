from app.database.session import SessionLocal
from sqlalchemy import text

db = SessionLocal()
res = db.execute(text("SELECT message, metadata FROM audit_logs WHERE status='failed' ORDER BY created_at DESC LIMIT 5")).fetchall()
for row in res:
    print(row[0], row[1])
