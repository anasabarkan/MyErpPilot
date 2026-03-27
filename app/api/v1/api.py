"""
API v1 aggregator — includes all v1 endpoint routers.
"""

from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.roles import router as roles_router
from app.api.v1.endpoints.approvals import router as approvals_router

api_v1_router = APIRouter()

api_v1_router.include_router(auth_router)
api_v1_router.include_router(roles_router, prefix="/roles", tags=["Roles"])
api_v1_router.include_router(approvals_router, prefix="/approvals", tags=["Approvals"])
