"""
Repository for Odoo DataSource persistence operations.
"""

from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.data_source import DataSource


def create_data_source(
    db: Session,
    *,
    name: str,
    odoo_url: str,
    database_name: str,
    username: str,
    api_key_encrypted: str,
    created_by: UUID,
    is_active: bool = False,
) -> DataSource:
    db_obj = DataSource(
        name=name,
        odoo_url=odoo_url,
        database_name=database_name,
        username=username,
        api_key_encrypted=api_key_encrypted,
        created_by=created_by,
        is_active=is_active,
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def get_data_source_by_id(db: Session, data_source_id: UUID) -> Optional[DataSource]:
    return db.query(DataSource).filter(DataSource.id == data_source_id).first()


def list_data_sources(db: Session, *, skip: int = 0, limit: int = 100) -> list[DataSource]:
    return (
        db.query(DataSource)
        .order_by(DataSource.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_active_data_source(db: Session) -> Optional[DataSource]:
    return db.query(DataSource).filter(DataSource.is_active.is_(True)).first()


def update_data_source(db: Session, db_obj: DataSource, update_data: dict[str, Any]) -> DataSource:
    for field, value in update_data.items():
        setattr(db_obj, field, value)

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def delete_data_source(db: Session, data_source_id: UUID) -> bool:
    db_obj = get_data_source_by_id(db, data_source_id)
    if not db_obj:
        return False

    db.delete(db_obj)
    db.commit()
    return True


def save_models_cache(db: Session, db_obj: DataSource, models: list[dict[str, str]]) -> DataSource:
    db_obj.models_cache = models
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def activate_data_source(db: Session, db_obj: DataSource) -> DataSource:
    db.query(DataSource).update({DataSource.is_active: False}, synchronize_session=False)
    db_obj.is_active = True
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

