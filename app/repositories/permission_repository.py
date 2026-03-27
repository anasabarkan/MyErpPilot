"""
Repository for permissions.
"""

from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.permission import Permission


def get_permission_by_name(db: Session, name: str) -> Permission | None:
    return db.scalars(select(Permission).where(Permission.name == name)).first()


def get_all_permissions(db: Session) -> Sequence[Permission]:
    return db.scalars(select(Permission)).all()


def create_permission(db: Session, name: str, description: str | None = None) -> Permission:
    db_perm = Permission(name=name, description=description)
    db.add(db_perm)
    db.commit()
    db.refresh(db_perm)
    return db_perm
