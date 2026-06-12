from app.database.session import SessionLocal
from app.repositories import data_source_repository as repo

db = SessionLocal()
ds = repo.get_active_data_source(db)
if ds:
    print("Found active data source. Updating URL...")
    repo.update_data_source(db, ds, {"odoo_url": "http://localhost:8069"})
    print("Update complete! New URL:", ds.odoo_url)
else:
    print("No active data source found.")
db.close()
