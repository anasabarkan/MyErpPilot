"""
Pydantic schemas for Role-related payloads.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.permission import PermissionResponse


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    description: str | None = None


class RoleResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RoleWithPermissions(RoleResponse):
    permissions: list[PermissionResponse] = []


class AssignRoleToUser(BaseModel):
    role_name: str
