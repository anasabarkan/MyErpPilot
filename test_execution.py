import traceback
from app.database.session import SessionLocal
from app.services.text_to_sql_service import TextToSQLService

def test_execution():
    db = SessionLocal()
    svc = TextToSQLService(db)
    # Get user id of admin
    from app.models.user import User
    admin = db.query(User).filter_by(email="admin@myerppilot.app").first()
    try:
        res = svc.query(question="Show me top 5 products", user_id=admin.id)
        print("Success!", res.summary)
    except Exception as e:
        traceback.print_exc()

if __name__ == '__main__':
    test_execution()
