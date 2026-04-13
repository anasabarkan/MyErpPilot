"""
User service — CRUD helpers for the User model.
"""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.repositories import user_repo
from app.services import audit_service


def get_user_by_email(db: Session, email: str) -> User | None:
    """Return the user with the given *email*, or ``None``."""
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: UUID) -> User | None:
    """Return the user with the given *user_id*, or ``None``."""
    return db.query(User).filter(User.id == user_id).first()


def list_users(db: Session) -> list[User]:
    """Return all users for the admin management table."""
    return list(user_repo.list_users(db))


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
    
    audit_service.log_sensitive_crud_event(
        db,
        action="create",
        entity_type="user",
        entity_id=str(user.id),
        user_id=user.id, # New user acts as subject and target here, or logged by None
    )
    
    return user


def update_user(
    db: Session,
    user: User,
    *,
    actor_user_id: UUID,
    email: str | None = None,
    full_name: str | None = None,
    avatar_url: str | None = None,
) -> User:
    """Update editable user fields and persist changes."""
    if email is not None:
        user.email = email
    if full_name is not None:
        user.full_name = full_name
    if avatar_url is not None:
        user.avatar_url = avatar_url

    db.commit()
    db.refresh(user)

    audit_service.log_sensitive_crud_event(
        db,
        action="update",
        entity_type="user",
        entity_id=str(user.id),
        user_id=actor_user_id,
    )
    return user


def set_user_active_status(
    db: Session,
    user: User,
    *,
    actor_user_id: UUID,
    is_active: bool,
) -> User:
    """Activate or deactivate a user account."""
    user.is_active = is_active
    db.commit()
    db.refresh(user)

    audit_service.log_sensitive_crud_event(
        db,
        action="activate" if is_active else "deactivate",
        entity_type="user",
        entity_id=str(user.id),
        user_id=actor_user_id,
        metadata={"is_active": is_active},
    )
    return user


def change_user_password(
    db: Session,
    user: User,
    *,
    actor_user_id: UUID,
    new_password: str,
    current_password: str | None = None,
    require_current_password: bool = False,
) -> User:
    """Change password for a local account user."""
    if user.provider != "local":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password updates are only supported for local accounts.",
        )

    if require_current_password:
        if not current_password or not user.hashed_password or not verify_password(
            current_password, user.hashed_password
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is invalid.",
            )
        if current_password == new_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password must be different from current password.",
            )

    user.hashed_password = hash_password(new_password)
    db.commit()
    db.refresh(user)

    audit_service.log_sensitive_crud_event(
        db,
        action="change_password",
        entity_type="user",
        entity_id=str(user.id),
        user_id=actor_user_id,
    )
    return user
