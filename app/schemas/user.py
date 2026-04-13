"""
Pydantic schemas for User-related request / response payloads.
"""

from datetime import datetime
from uuid import UUID

from typing import Any
from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Request schemas ──────────────────────────────

class UserCreate(BaseModel):
    """Payload for local user registration."""
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class UserUpdate(BaseModel):
    """Payload for admin-level user profile edits."""
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role_name: str | None = Field(default=None, min_length=2, max_length=50)


class UserStatusUpdate(BaseModel):
    """Payload for activating/deactivating users."""
    is_active: bool


class UserSelfUpdate(BaseModel):
    """Payload for current user profile edits."""
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=500)


class UserPasswordUpdate(BaseModel):
    """Payload for password updates initiated by the current user."""
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


# ── Response schemas ─────────────────────────────

class UserResponse(BaseModel):
    """Public representation of a user (no password hash)."""
    id: UUID
    email: str
    full_name: str
    is_active: bool
    is_verified: bool
    provider: str
    avatar_url: str | None = None
    roles: list[str] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("roles", mode="before")
    @classmethod
    def get_role_names(cls, v: Any) -> list[str]:
        """Convert list of Role objects to list of role names strings."""
        if isinstance(v, list):
            return [role.name if hasattr(role, "name") else str(role) for role in v]
        return v
