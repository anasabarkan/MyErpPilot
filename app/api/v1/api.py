"""
API v1 aggregator — includes all v1 endpoint routers.
"""

from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.users import router as users_router
from app.api.v1.endpoints.roles import router as roles_router
from app.api.v1.endpoints.approvals import router as approvals_router
from app.api.v1.endpoints.registration_requests import router as registration_requests_router
from app.api.v1.endpoints.audit import router as audit_router
from app.api.v1.endpoints.dashboard import router as dashboard_router
from app.api.v1.endpoints.data_sources import router as data_sources_router
from app.api.v1.endpoints.ai import router as ai_router
from app.api.v1.endpoints.chat import router as chat_router

api_v1_router = APIRouter()

api_v1_router.include_router(auth_router, tags=["Authentication"])
api_v1_router.include_router(users_router, prefix="/users", tags=["Users"])
api_v1_router.include_router(roles_router, prefix="/roles", tags=["Roles"])
api_v1_router.include_router(approvals_router, prefix="/approvals", tags=["Approvals"])
api_v1_router.include_router(
    registration_requests_router,
    tags=["Registration Requests"],
)
api_v1_router.include_router(audit_router, prefix="/audit", tags=["Audit"])
api_v1_router.include_router(dashboard_router, prefix="/dashboard", tags=["Dashboard"])
api_v1_router.include_router(data_sources_router, prefix="/data-sources", tags=["Data Sources"])
api_v1_router.include_router(ai_router, prefix="/ai", tags=["AI"])
api_v1_router.include_router(chat_router, prefix="/chat/sessions", tags=["Chat"])
