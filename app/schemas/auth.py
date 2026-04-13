"""
Pydantic schemas for authentication payloads (login, tokens).
"""

from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field, field_validator

from app.schemas.user import UserResponse


class UserLogin(BaseModel):
    """Payload for local email/password login."""
    email: str = Field(validation_alias=AliasChoices("email", "username"), min_length=1)
    password: str = Field(..., min_length=1)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class FirstLoginPasswordChange(BaseModel):
    """Payload used to complete first-login password reset."""
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """Decoded JWT payload (internal use)."""
    sub: UUID        # user id
    exp: int         # expiration timestamp


class CurrentUserResponse(UserResponse):
    """Authenticated user payload enriched with resolved RBAC permissions."""
    permissions: list[str] = []
