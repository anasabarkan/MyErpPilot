import traceback
from app.database.session import SessionLocal
from app.services.ai_orchestrator_service import AIOrchestratorService
from app.models.user import User

def test():
    db = SessionLocal()
    orchestrator = AIOrchestratorService(db)
    admin = db.query(User).filter_by(email="admin@myerppilot.app").first()
    
    try:
        res = orchestrator.query(question="show top products", user_id=admin.id)
        print("Success!", res.summary)
    except Exception as e:
        traceback.print_exc()

if __name__ == '__main__':
    test()
