from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_v1_router
from app.core.config import settings
from app.database.session import Base, engine

# Import models so SQLAlchemy registers them with Base.metadata
import app.models.user  # noqa: F401 — User


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables and run seeding on startup."""
    print("--- [STARTUP] Initializing Database ---")
    Base.metadata.create_all(bind=engine)
    
    # --- Auto-Seeding ---
    import os
    import importlib.util
    from app.database.session import SessionLocal

    seeds_dir = os.path.join(os.path.dirname(__file__), "scripts", "seeds")
    if os.path.exists(seeds_dir):
        print(f"--- [STARTUP] Running seeds from {seeds_dir} ---")
        db = SessionLocal()
        try:
            for filename in sorted(os.listdir(seeds_dir)):
                if filename.endswith(".py") and filename != "__init__.py":
                    module_name = f"app.scripts.seeds.{filename[:-3]}"
                    print(f"  * Executing {module_name}...")
                    spec = importlib.util.spec_from_file_location(module_name, os.path.join(seeds_dir, filename))
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    if hasattr(module, "seed"):
                        module.seed(db)
        except Exception as e:
            print(f"--- [ERROR] Seeding failed: {e} ---")
        finally:
            db.close()
    
    yield
    print("--- [SHUTDOWN] Cleaning up ---")


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routers ──────────────────────────────────
app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/", tags=["Health"])
def health_check():
    """Root endpoint — health check."""
    return {"status": "ok", "message": "My ERP Pilot is running."}


# ── OpenAPI Customization ────────────────────────
from fastapi.openapi.utils import get_openapi


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        contact=app.contact,
        license_info=app.license_info,
    )
    # Define BearerAuth manually
    openapi_schema["components"]["securitySchemes"]["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }

    # Ensure all endpoints that require security can also use BearerAuth
    # This specifically fixes the issue where Swagger UI doesn't send the token
    # for manually authorized "BearerAuth" if the endpoint only lists OAuth2.
    for path in openapi_schema.get("paths", {}).values():
        for method in path.values():
            if "security" in method:
                # Add BearerAuth as an alternative option
                method["security"].append({"BearerAuth": []})

    app.openapi_schema = openapi_schema
    return openapi_schema


app.openapi = custom_openapi
