"""
API v1 aggregator — includes all v1 endpoint routers.
"""

from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router

api_v1_router = APIRouter()

api_v1_router.include_router(auth_router)
