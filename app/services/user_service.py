"""
User service — CRUD helpers for the User model.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.user import User


def get_user_by_email(db: Session, email: str) -> User | None:
    """Return the user with the given *email*, or ``None``."""
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: UUID) -> User | None:
    """Return the user with the given *user_id*, or ``None``."""
    return db.query(User).filter(User.id == user_id).first()


def create_user(
    db: Session,
    *,
    email: str,
    full_name: str,
    password: str | None = None,
    provider: str = "local",
    provider_id: str | None = None,
    avatar_url: str | None = None,
    is_verified: bool = False,
) -> User:
    """
    Create and persist a new user.

    For local accounts supply *password*; for OAuth accounts supply
    *provider*, *provider_id* and optionally *avatar_url*.
    """
    user = User(
        email=email,
        full_name=full_name,
        hashed_password=hash_password(password) if password else None,
        provider=provider,
        provider_id=provider_id,
        avatar_url=avatar_url,
        is_verified=is_verified,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
