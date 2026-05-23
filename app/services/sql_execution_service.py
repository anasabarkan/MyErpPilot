"""
Read-only SQL execution against the Odoo PostgreSQL database.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from time import perf_counter
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.services.sql_validation_service import ValidatedSQL


class SQLExecutionError(RuntimeError):
    """Raised when validated SQL cannot be executed."""


class SQLExecutionService:
    def __init__(self, *, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(settings.odoo_sql_database_url, pool_pre_ping=True)

    def execute(self, validated_sql: ValidatedSQL) -> tuple[list[dict[str, Any]], int]:
        started = perf_counter()
        try:
            with self.engine.connect() as connection:
                connection.execute(text("BEGIN READ ONLY"))
                connection.execute(
                    text("SET LOCAL statement_timeout = :timeout_ms"),
                    {"timeout_ms": max(1, settings.TEXT2SQL_TIMEOUT_SECONDS) * 1000},
                )
                result = connection.execute(text(validated_sql.sql))
                rows = [dict(row._mapping) for row in result.fetchall()]
                connection.execute(text("COMMIT"))
        except Exception as exc:  # noqa: BLE001 - converted to controlled service error
            raise SQLExecutionError("Validated SQL execution failed.") from exc

        elapsed_ms = int((perf_counter() - started) * 1000)
        return [self._json_safe_row(row) for row in rows], elapsed_ms

    @classmethod
    def _json_safe_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        return {str(key): cls._json_safe_value(value) for key, value in row.items()}

    @staticmethod
    def _json_safe_value(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value
