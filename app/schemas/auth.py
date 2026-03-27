"""
Pydantic schemas for authentication payloads (login, tokens).
"""

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class UserLogin(BaseModel):
    """Payload for local email/password login."""
    email: EmailStr
    password: str = Field(..., min_length=1)


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """Decoded JWT payload (internal use)."""
    sub: UUID        # user id
    exp: int         # expiration timestamp
