"""
Pydantic schemas for Permission-related payloads.
"""

from uuid import UUID
from pydantic import BaseModel, ConfigDict


class PermissionResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None

    model_config = ConfigDict(from_attributes=True)
