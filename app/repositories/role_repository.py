"""
Repository for roles.
"""

from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.role import Role
from app.models.user import User
from app.models.permission import Permission


def create_role(db: Session, name: str, description: str | None = None) -> Role:
    db_role = Role(name=name, description=description)
    db.add(db_role)
    db.commit()
    db.refresh(db_role)
    return db_role


def get_role_by_name(db: Session, name: str) -> Role | None:
    return db.scalars(select(Role).where(Role.name == name)).first()


def get_all_roles(db: Session) -> Sequence[Role]:
    return db.scalars(select(Role)).all()


def assign_role_to_user(db: Session, user_id: str |  UUID, role_id: str | UUID) -> None:
    # Get user
    user = db.get(User, user_id)
    role = db.get(Role, role_id)
    if user and role and role not in user.roles:
        user.roles.append(role)
        db.commit()


def get_user_roles(db: Session, user_id: str | UUID) -> Sequence[Role]:
    user = db.scalars(select(User).options(joinedload(User.roles)).where(User.id == user_id)).first()
    if user:
        return user.roles
    return []


def get_user_permissions(db: Session, user_id: str | UUID) -> set[str]:
    """Retrieve all permission names granted to a user via their roles."""
    user = db.scalars(
        select(User).options(joinedload(User.roles).joinedload(Role.permissions)).where(User.id == user_id)
    ).first()

    if not user:
        return set()

    permissions = set()
    for role in user.roles:
        for perm in role.permissions:
            permissions.add(perm.name)
    return permissions
