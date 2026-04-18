"""
Service layer for Odoo DataSource operations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.integrations.odoo import (
    OdooAuthenticationError,
    OdooConnectionError,
    OdooJsonRpcClient,
    OdooRequestError,
)
from app.models.data_source import DataSource
from app.repositories import data_source_repository as repo
from app.schemas.data_source import DataSourceConnectionTestRequest, DataSourceCreate, DataSourceUpdate
from app.security.encryption import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)


def _normalize_odoo_url(url: str) -> str:
    return url.strip().rstrip("/")


def _build_client(
    *,
    odoo_url: str,
    database_name: str,
    username: str,
    api_key: str,
) -> OdooJsonRpcClient:
    return OdooJsonRpcClient(
        base_url=_normalize_odoo_url(odoo_url),
        database_name=database_name.strip(),
        username=username.strip(),
        api_key=api_key,
    )


def _models_from_odoo(
    *,
    odoo_url: str,
    database_name: str,
    username: str,
    api_key: str,
) -> list[dict[str, str]]:
    client = _build_client(
        odoo_url=odoo_url,
        database_name=database_name,
        username=username,
        api_key=api_key,
    )
    user_id = client.authenticate()
    return client.list_models(user_id)


def test_connection_payload(payload: DataSourceConnectionTestRequest) -> dict[str, object]:
    models = _models_from_odoo(
        odoo_url=payload.odoo_url,
        database_name=payload.database_name,
        username=payload.username,
        api_key=payload.api_key,
    )
    return {
        "success": True,
        "message": "Connection successful.",
        "models": models,
    }


def create_data_source(db: Session, obj_in: DataSourceCreate, created_by: UUID) -> DataSource:
    encrypted_api_key = encrypt_value(obj_in.api_key)
    return repo.create_data_source(
        db,
        name=obj_in.name.strip(),
        odoo_url=_normalize_odoo_url(obj_in.odoo_url),
        database_name=obj_in.database_name.strip(),
        username=obj_in.username.strip(),
        api_key_encrypted=encrypted_api_key,
        created_by=created_by,
        is_active=False,
    )


def update_data_source(db: Session, db_obj: DataSource, obj_in: DataSourceUpdate) -> DataSource:
    update_data: dict[str, object] = {}
    if obj_in.name is not None:
        update_data["name"] = obj_in.name.strip()
    if obj_in.odoo_url is not None:
        update_data["odoo_url"] = _normalize_odoo_url(obj_in.odoo_url)
    if obj_in.database_name is not None:
        update_data["database_name"] = obj_in.database_name.strip()
    if obj_in.username is not None:
        update_data["username"] = obj_in.username.strip()
    if obj_in.api_key is not None:
        update_data["api_key_encrypted"] = encrypt_value(obj_in.api_key)

    if not update_data:
        return db_obj

    return repo.update_data_source(db, db_obj, update_data)


def _decrypt_data_source_api_key(data_source: DataSource) -> str:
    try:
        return decrypt_value(data_source.api_key_encrypted)
    except ValueError as exc:
        logger.error("Failed to decrypt API key for data source %s", data_source.id)
        raise OdooRequestError("Stored Odoo credentials are invalid.") from exc


def _test_and_get_models_for_data_source(data_source: DataSource) -> list[dict[str, str]]:
    api_key = _decrypt_data_source_api_key(data_source)
    return _models_from_odoo(
        odoo_url=data_source.odoo_url,
        database_name=data_source.database_name,
        username=data_source.username,
        api_key=api_key,
    )


def test_saved_data_source(db: Session, data_source: DataSource) -> dict[str, object]:
    models = _test_and_get_models_for_data_source(data_source)
    update_data = {
        "models_cache": models,
        "last_tested_at": datetime.now(timezone.utc),
    }
    repo.update_data_source(db, data_source, update_data)

    return {
        "success": True,
        "message": "Connection successful.",
        "models": models,
    }


def get_data_source_models(db: Session, data_source: DataSource, refresh: bool = False) -> list[dict[str, str]]:
    if not refresh and isinstance(data_source.models_cache, list) and data_source.models_cache:
        return data_source.models_cache

    models = _test_and_get_models_for_data_source(data_source)
    update_data = {
        "models_cache": models,
        "last_tested_at": datetime.now(timezone.utc),
    }
    repo.update_data_source(db, data_source, update_data)
    return models


def activate_data_source(db: Session, data_source: DataSource) -> DataSource:
    return repo.activate_data_source(db, data_source)


def get_data_source_or_none(db: Session, data_source_id: UUID) -> DataSource | None:
    return repo.get_data_source_by_id(db, data_source_id)


def list_data_sources(db: Session, *, skip: int = 0, limit: int = 100) -> list[DataSource]:
    return repo.list_data_sources(db, skip=skip, limit=limit)


def get_active_data_source(db: Session) -> DataSource | None:
    return repo.get_active_data_source(db)


def delete_data_source(db: Session, data_source_id: UUID) -> bool:
    return repo.delete_data_source(db, data_source_id)


def sanitize_odoo_error_message(exc: Exception) -> str:
    if isinstance(exc, OdooAuthenticationError):
        return "Odoo authentication failed. Check database, username, and API key."
    if isinstance(exc, OdooConnectionError):
        return "Unable to connect to Odoo. Check URL/network availability."
    if isinstance(exc, OdooRequestError):
        return str(exc)
    if isinstance(exc, RuntimeError):
        return str(exc)
    return "Unexpected Odoo integration error."
