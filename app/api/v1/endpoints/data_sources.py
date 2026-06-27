"""
API endpoints for Odoo data source management.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_any_permission, require_permission
from app.database.session import get_db
from app.integrations.odoo import OdooAuthenticationError, OdooConnectionError, OdooRequestError
from app.models.user import User
from app.schemas.data_source import (
    ActiveDataSourceResponse,
    DataSourceConnectionTestRequest,
    DataSourceCreate,
    DataSourceModelsResponse,
    DataSourceResponse,
    DataSourceTestResponse,
    DataSourceUpdate,
)
from app.services import data_source_service as service

router = APIRouter()


def _raise_http_from_odoo_error(exc: Exception) -> None:
    message = service.sanitize_odoo_error_message(exc)
    if isinstance(exc, OdooConnectionError):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message) from exc
    if isinstance(exc, (OdooAuthenticationError, OdooRequestError, ValueError, RuntimeError)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message) from exc


@router.post(
    "/test-connection",
    response_model=DataSourceTestResponse,
    dependencies=[Depends(require_any_permission("manage_odoo_sources", "manage_data_sources", "test_data_source"))],
)
def test_connection_with_payload(
    *,
    payload: DataSourceConnectionTestRequest,
) -> Any:
    """
    Validate Odoo credentials without persisting a data source.
    """
    try:
        return service.test_connection_payload(payload)
    except Exception as exc:  # noqa: BLE001 - converted to sanitized HTTP errors
        _raise_http_from_odoo_error(exc)


@router.get(
    "/",
    response_model=list[DataSourceResponse],
    dependencies=[Depends(require_any_permission("manage_odoo_sources", "manage_data_sources", "view_data_sources"))],
)
def list_data_sources(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> Any:
    """
    List all Odoo data sources (admin-managed/global scope).
    """
    return service.list_data_sources(db, skip=skip, limit=limit)


@router.get(
    "/active",
    response_model=ActiveDataSourceResponse,
    dependencies=[Depends(require_any_permission("manage_odoo_sources", "manage_data_sources", "view_data_sources"))],
)
def get_active_data_source(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> Any:
    """
    Retrieve the globally active Odoo data source.
    """
    return {"active_data_source": service.get_active_data_source(db)}


@router.post(
    "/",
    response_model=DataSourceResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_any_permission("manage_odoo_sources", "manage_data_sources"))],
)
def create_data_source(
    *,
    db: Session = Depends(get_db),
    data_source_in: DataSourceCreate,
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Create and store a new Odoo data source.
    """
    try:
        return service.create_data_source(db, data_source_in, current_user.id)
    except Exception as exc:  # noqa: BLE001 - converted to sanitized HTTP errors
        _raise_http_from_odoo_error(exc)


@router.get(
    "/{id}",
    response_model=DataSourceResponse,
    dependencies=[Depends(require_any_permission("manage_odoo_sources", "manage_data_sources", "view_data_sources"))],
)
def get_data_source(
    id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> Any:
    """
    Get a data source by id.
    """
    data_source = service.get_data_source_or_none(db, id)
    if not data_source:
        raise HTTPException(status_code=404, detail="Data source not found.")
    return data_source


@router.patch(
    "/{id}",
    response_model=DataSourceResponse,
    dependencies=[Depends(require_any_permission("manage_odoo_sources", "manage_data_sources"))],
)
def update_data_source(
    id: UUID,
    *,
    db: Session = Depends(get_db),
    data_source_in: DataSourceUpdate,
    _current_user: User = Depends(get_current_user),
) -> Any:
    """
    Update an existing Odoo data source.
    """
    data_source = service.get_data_source_or_none(db, id)
    if not data_source:
        raise HTTPException(status_code=404, detail="Data source not found.")

    try:
        return service.update_data_source(db, data_source, data_source_in)
    except Exception as exc:  # noqa: BLE001 - converted to sanitized HTTP errors
        _raise_http_from_odoo_error(exc)


@router.delete(
    "/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(require_any_permission("manage_odoo_sources", "manage_data_sources"))],
)
def delete_data_source(
    id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> None:
    """
    Delete an Odoo data source.
    """
    deleted = service.delete_data_source(db, id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Data source not found.")
    return None


@router.post(
    "/{id}/test",
    response_model=DataSourceTestResponse,
    dependencies=[Depends(require_any_permission("manage_odoo_sources", "manage_data_sources", "test_data_source"))],
)
def test_saved_data_source(
    id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> Any:
    """
    Validate a saved Odoo source and refresh its model cache.
    """
    data_source = service.get_data_source_or_none(db, id)
    if not data_source:
        raise HTTPException(status_code=404, detail="Data source not found.")

    try:
        return service.test_saved_data_source(db, data_source)
    except Exception as exc:  # noqa: BLE001 - converted to sanitized HTTP errors
        _raise_http_from_odoo_error(exc)


@router.get(
    "/{id}/models",
    response_model=DataSourceModelsResponse,
    dependencies=[Depends(require_any_permission("manage_odoo_sources", "manage_data_sources", "view_data_sources", "test_data_source"))],
)
def get_data_source_models(
    id: UUID,
    refresh: bool = False,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> Any:
    """
    Return models for a saved Odoo source (cached unless refresh=true).
    """
    data_source = service.get_data_source_or_none(db, id)
    if not data_source:
        raise HTTPException(status_code=404, detail="Data source not found.")

    try:
        models = service.get_data_source_models(db, data_source, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - converted to sanitized HTTP errors
        _raise_http_from_odoo_error(exc)

    return {"models": models}


@router.post(
    "/{id}/activate",
    response_model=DataSourceResponse,
    dependencies=[Depends(require_permission("manage_odoo_sources"))],
)
def activate_data_source(
    id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> Any:
    """
    Mark a source as globally active and deactivate all others.
    """
    data_source = service.get_data_source_or_none(db, id)
    if not data_source:
        raise HTTPException(status_code=404, detail="Data source not found.")

    return service.activate_data_source(db, data_source)