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
    title="My ERP Pilot - Enterprise AI Backend",
    description=(
        "## My ERP Pilot 2.0\n\n"
        "Welcome to the My ERP Pilot API documentation. This system provides:\n\n"
        "* **AI Orchestration**: Multi-agent pipelines using LangGraph.\n"
        "* **Enterprise Security**: JWT-based authentication and RBAC.\n"
        "* **Human-in-the-Loop**: Approval workflows for sensitive actions.\n"
        "* **Audit Logging**: Comprehensive traceability for administrative events.\n\n"
        "### Support\n"
        "For support or feedback, contact the engineering team at dev-ops@myerppilot.com."
    ),
    version="2.1.0",
    lifespan=lifespan,
    contact={
        "name": "My ERP Pilot Support",
        "url": "https://github.com/anasabarkan/MyErpPilot",
        "email": "support@myerppilot.com",
    },
    license_info={
        "name": "Proprietary",
        "url": "https://myerppilot.com/license",
    },
    swagger_ui_parameters={"defaultModelsExpandDepth": -1}, # Hide standard Pydantic models in Swagger
)

# ── API routers ──────────────────────────────────
app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/", tags=["Health"])
def health_check():
    """Root endpoint — health check."""
    return {"status": "ok", "message": "My ERP Pilot is running."}
