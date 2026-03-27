"""
Business logic service for managing roles and permissions.
"""

from typing import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.role import Role
from app.models.user import User
from app.repositories import role_repository
from app.services import audit_service


def assign_role_to_user(db: Session, user_id: str | UUID, role_name: str) -> None:
    role = role_repository.get_role_by_name(db, name=role_name)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{role_name}' not found",
        )
    
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    role_repository.assign_role_to_user(db, user_id=user.id, role_id=role.id)
    
    audit_service.log_role_event(
        db,
        action="assign_role",
        user_id=user.id, # system or current user depending on route, fallback to target user
        target_user_id=user.id,
        role_name=role_name
    )


def remove_role_from_user(db: Session, user_id: str | UUID, role_name: str) -> None:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    role = role_repository.get_role_by_name(db, name=role_name)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{role_name}' not found",
        )

    if role in user.roles:
        user.roles.remove(role)
        db.commit()
        
        audit_service.log_role_event(
            db,
            action="remove_role",
            user_id=user.id,
            target_user_id=user.id,
            role_name=role_name
        )


def list_roles(db: Session) -> Sequence[Role]:
    return role_repository.get_all_roles(db)


def get_user_permissions_list(db: Session, user_id: str | UUID) -> list[str]:
    permissions_set = role_repository.get_user_permissions(db, user_id=user_id)
    return list(permissions_set)


def get_user_roles(db: Session, user_id: str | UUID) -> Sequence[Role]:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return role_repository.get_user_roles(db, user_id=user.id)
