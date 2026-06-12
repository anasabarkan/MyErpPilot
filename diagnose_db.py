"""Quick diagnostic: list PostgreSQL databases and roles."""
from sqlalchemy import create_engine, text

e = create_engine("postgresql://postgres:postgres@localhost:5432/postgres")
c = e.connect()

rows = c.execute(text("SELECT datname FROM pg_database WHERE datistemplate = false")).fetchall()
print("Databases:", [r[0] for r in rows])

roles = c.execute(text("SELECT rolname FROM pg_roles WHERE rolcanlogin = true")).fetchall()
print("Login roles:", [r[0] for r in roles])

c.close()
