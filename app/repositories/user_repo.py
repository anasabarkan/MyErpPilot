"""
Repository helpers for user queries.
"""

from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.user import User


def list_users(db: Session) -> Sequence[User]:
    """Return all users with their roles eagerly loaded for table display."""
    return db.scalars(
        select(User)
        .options(joinedload(User.roles))
        .order_by(User.created_at.desc(), User.email.asc())
    ).unique().all()
