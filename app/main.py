from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.api import api_v1_router
from app.database.session import Base, engine

# Import models so SQLAlchemy registers them with Base.metadata
import app.models.user  # noqa: F401 — User


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

# ── API routers ──────────────────────────────────
app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/", tags=["Health"])
def health_check():
    """Root endpoint — health check."""
    return {"status": "ok", "message": "My ERP Pilot is running."}
