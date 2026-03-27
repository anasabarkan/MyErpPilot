"""
Repository for DataSource operations.
"""

from typing import List, Optional, Dict, Any
from uuid import UUID

from sqlalchemy.orm import Session
from app.models.data_source import DataSource
from app.schemas.data_source import DataSourceCreate, DataSourceUpdate


def create_data_source(db: Session, obj_in: DataSourceCreate, owner_id: UUID) -> DataSource:
    """
    Create a new data source connection.
    """
    db_obj = DataSource(
        name=obj_in.name,
        db_type=obj_in.db_type,
        host=obj_in.host,
        port=obj_in.port,
        database=obj_in.database,
        username=obj_in.username,
        password=obj_in.password,  # Storing as plain for now; structure for encryption if needed
        is_active=obj_in.is_active,
        owner_id=owner_id,
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def get_data_source_by_id(db: Session, data_source_id: UUID) -> Optional[DataSource]:
    """
    Retrieve a data source by its ID.
    """
    return db.query(DataSource).filter(DataSource.id == data_source_id).first()


def list_data_sources_by_user(
    db: Session, owner_id: UUID, skip: int = 0, limit: int = 100
) -> List[DataSource]:
    """
    Get all data sources for a given user.
    """
    return (
        db.query(DataSource)
        .filter(DataSource.owner_id == owner_id)
        .offset(skip)
        .limit(limit)
        .all()
    )


def update_data_source(
    db: Session, db_obj: DataSource, obj_in: DataSourceUpdate
) -> DataSource:
    """
    Update an existing data source.
    """
    update_data = obj_in.model_dump(exclude_unset=True)
    
    for field, value in update_data.items():
        setattr(db_obj, field, value)

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def delete_data_source(db: Session, data_source_id: UUID) -> bool:
    """
    Delete a data source.
    """
    db_obj = get_data_source_by_id(db, data_source_id)
    if not db_obj:
        return False
        
    db.delete(db_obj)
    db.commit()
    return True


def save_schema_cache(db: Session, data_source_id: UUID, schema_cache: Dict[str, Any]) -> Optional[DataSource]:
    """
    Save the extracted schema to the DataSource record.
    """
    db_obj = get_data_source_by_id(db, data_source_id)
    if not db_obj:
        return None
        
    db_obj.schema_cache = schema_cache
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj
