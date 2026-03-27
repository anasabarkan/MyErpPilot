"""
API Endpoints for DataSource management.
"""

from typing import Any, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_permission
from app.database.session import get_db
from app.models.user import User
from app.schemas.data_source import (
    DataSourceCreate,
    DataSourceResponse,
    DataSourceUpdate,
    DataSourceTestResponse,
    DataSourceSchemaResponse,
)
from app.repositories import data_source_repository as repo
from app.services import data_source_service as service

router = APIRouter()


@router.post("/", response_model=DataSourceResponse, status_code=status.HTTP_201_CREATED)
def create_data_source(
    *,
    db: Session = Depends(get_db),
    data_source_in: DataSourceCreate,
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Create a new external data source connection.
    """
    data_source = repo.create_data_source(
        db=db, obj_in=data_source_in, owner_id=current_user.id
    )
    # Mask password for response parsing
    ds_resp = DataSourceResponse.from_orm(data_source)
    ds_resp.has_schema_cache = bool(data_source.schema_cache)
    return ds_resp


@router.get("/", response_model=List[DataSourceResponse])
def list_data_sources(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    List data sources for the current user.
    """
    data_sources = repo.list_data_sources_by_user(
        db=db, owner_id=current_user.id, skip=skip, limit=limit
    )
    
    results = []
    for ds in data_sources:
        ds_resp = DataSourceResponse.from_orm(ds)
        ds_resp.has_schema_cache = bool(ds.schema_cache)
        results.append(ds_resp)
        
    return results


@router.get("/{id}", response_model=DataSourceResponse)
def get_data_source(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Get a specific data source by ID.
    """
    data_source = repo.get_data_source_by_id(db=db, data_source_id=id)
    if not data_source:
        raise HTTPException(status_code=404, detail="DataSource not found")
        
    if data_source.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
        
    ds_resp = DataSourceResponse.from_orm(data_source)
    ds_resp.has_schema_cache = bool(data_source.schema_cache)
    return ds_resp


@router.put("/{id}", response_model=DataSourceResponse)
def update_data_source(
    id: UUID,
    *,
    db: Session = Depends(get_db),
    data_source_in: DataSourceUpdate,
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Update a data source.
    """
    data_source = repo.get_data_source_by_id(db=db, data_source_id=id)
    if not data_source:
        raise HTTPException(status_code=404, detail="DataSource not found")
        
    if data_source.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
        
    data_source = repo.update_data_source(db=db, db_obj=data_source, obj_in=data_source_in)
    
    ds_resp = DataSourceResponse.from_orm(data_source)
    ds_resp.has_schema_cache = bool(data_source.schema_cache)
    return ds_resp


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_data_source(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Delete a data source.
    """
    data_source = repo.get_data_source_by_id(db=db, data_source_id=id)
    if not data_source:
        raise HTTPException(status_code=404, detail="DataSource not found")
        
    if data_source.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
        
    repo.delete_data_source(db=db, data_source_id=id)
    return None


@router.post("/{id}/test", response_model=DataSourceTestResponse)
def test_data_source_connection(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Test the connection to an external database.
    """
    data_source = repo.get_data_source_by_id(db=db, data_source_id=id)
    if not data_source:
        raise HTTPException(status_code=404, detail="DataSource not found")
        
    if data_source.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
        
    result = service.test_connection(db=db, data_source=data_source)
    return result


@router.get("/{id}/schema", response_model=DataSourceSchemaResponse)
def get_data_source_schema(
    id: UUID,
    refresh: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Get the tables and columns for the database.
    Optionally pass `?refresh=true` to force a new pull.
    """
    data_source = repo.get_data_source_by_id(db=db, data_source_id=id)
    if not data_source:
        raise HTTPException(status_code=404, detail="DataSource not found")
        
    if data_source.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
        
    # Standard pull from cache if it exists and refresh is not requested
    if not refresh and data_source.schema_cache:
        return data_source.schema_cache
        
    # Otherwise, fresh extraction
    try:
        schema_data = service.cache_schema(db=db, data_source_id=id)
        return schema_data
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract schema: {str(e)}")
