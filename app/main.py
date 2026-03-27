from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.database.session import Base, engine


# Import models so SQLAlchemy registers them with Base.metadata
import app.database.schema            # noqa: F401 — Product, SalesHistory, PurchaseOrder
import app.database.audit_logs        # noqa: F401 — AuditLog
import app.validation.validation_queue # noqa: F401 — ValidationItem


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup."""
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="My ERP Pilot",
    description=(
        "AI orchestration layer with LangGraph multi-agent pipeline, "
        "human-in-the-loop validation, and PostgreSQL audit logging."
    ),
    version="2.0.0",
    lifespan=lifespan,
)




@app.get("/", tags=["Health"])
def health_check():
    """Root endpoint — health check."""
    return {"status": "ok", "message": "Omnidata AI Engine is running."}
