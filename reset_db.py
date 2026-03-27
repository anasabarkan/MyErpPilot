from sqlalchemy import text
from app.database.session import engine, Base
import app.models.user
import app.models.role
import app.models.permission
import app.models.user_role
import app.models.role_permission
import app.models.data_source
import app.models.audit_log
import app.models.approval

def reset_db():
    print("Dropping existing tables...")
    try:
        # We need to drop tables in the right order because of FKs, 
        # or just drop everything with CASCADE if possible.
        # But create_all/drop_all handles it if models are imported.
        Base.metadata.drop_all(bind=engine)
        print("Successfully dropped all tables.")
    except Exception as e:
        print(f"Error dropping tables: {e}")
        print("Attempting manual drop for 'users'...")
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
            conn.commit()

if __name__ == "__main__":
    reset_db()
