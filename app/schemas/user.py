"""
Pydantic schemas for User-related request / response payloads.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# ── Request schemas ──────────────────────────────

class UserCreate(BaseModel):
    """Payload for local user registration."""
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


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
