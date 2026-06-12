from app.database.session import SessionLocal
from app.repositories import data_source_repository as repo

db = SessionLocal()
ds = repo.get_active_data_source(db)
if ds:
    print(f"Old URL: {ds.odoo_url}")
    ds.odoo_url = "http://localhost:8069"
    db.add(ds)
    db.commit()
    print(f"New URL saved: {ds.odoo_url}")
else:
    print("No active data source found.")
db.close()
