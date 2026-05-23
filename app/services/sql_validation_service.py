"""
Validation for LLM-generated read-only Odoo SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from app.services.text_to_sql_schema_service import allowed_columns_by_table, allowed_table_names


class SQLValidationError(ValueError):
    """Raised when generated SQL is not safe to execute."""


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    limit: int


_FORBIDDEN_TOKENS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|copy|call|do|create|grant|revoke|merge|execute|vacuum|analyze)\b",
    re.IGNORECASE,
)


class SQLValidationService:
    def __init__(self, *, max_rows: int) -> None:
        self.max_rows = max(1, max_rows)
        self.allowed_tables = allowed_table_names()
        self.allowed_columns = allowed_columns_by_table()

    def validate(self, sql: str) -> ValidatedSQL:
        cleaned = self._clean_sql(sql)
        if not cleaned:
            raise SQLValidationError("Generated SQL is empty.")
        if ";" in cleaned.rstrip(";"):
            raise SQLValidationError("Only one SQL statement is allowed.")
        if _FORBIDDEN_TOKENS.search(cleaned):
            raise SQLValidationError("Only read-only SELECT queries are allowed.")

        try:
            parsed = sqlglot.parse_one(cleaned, read="postgres")
        except sqlglot.errors.ParseError as exc:
            raise SQLValidationError("Generated SQL could not be parsed.") from exc

        if not isinstance(parsed, (exp.Select, exp.With)):
            raise SQLValidationError("Query must be a SELECT statement.")
        if isinstance(parsed, exp.With) and not isinstance(parsed.this, exp.Select):
            raise SQLValidationError("WITH queries must end in SELECT.")

        tables = {table.name for table in parsed.find_all(exp.Table)}
        disallowed_tables = sorted(table for table in tables if table not in self.allowed_tables)
        if disallowed_tables:
            raise SQLValidationError(f"Query references unsupported table(s): {', '.join(disallowed_tables)}.")

        self._validate_columns(parsed)
        limit = self._extract_limit(parsed)
        if limit is None:
            cleaned = f"{cleaned.rstrip(';')} LIMIT {self.max_rows}"
            limit = self.max_rows
        elif limit > self.max_rows:
            raise SQLValidationError(f"Query LIMIT must be <= {self.max_rows}.")

        return ValidatedSQL(sql=cleaned.rstrip(";"), limit=limit)

    @staticmethod
    def _clean_sql(sql: str) -> str:
        cleaned = sql.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:sql)?", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        return cleaned.rstrip(";").strip()

    def _validate_columns(self, parsed: exp.Expression) -> None:
        table_aliases: dict[str, str] = {}
        for table in parsed.find_all(exp.Table):
            table_name = table.name
            alias = table.alias_or_name
            table_aliases[alias] = table_name
            table_aliases[table_name] = table_name

        if not table_aliases:
            raise SQLValidationError("Query must reference at least one allowed table.")

        output_aliases = {
            alias
            for alias in (select_alias.alias for select_alias in parsed.find_all(exp.Alias))
            if alias
        }
        all_allowed_columns = set().union(*self.allowed_columns.values())
        for column in parsed.find_all(exp.Column):
            column_name = column.name
            if column_name in output_aliases:
                continue
            table_ref = column.table
            if table_ref:
                table_name = table_aliases.get(table_ref)
                if not table_name:
                    raise SQLValidationError(f"Unknown table alias '{table_ref}'.")
                if column_name not in self.allowed_columns.get(table_name, set()):
                    raise SQLValidationError(f"Column '{table_ref}.{column_name}' is not allowed.")
                continue

            if column_name not in all_allowed_columns:
                raise SQLValidationError(f"Column '{column_name}' is not allowed.")

    @staticmethod
    def _extract_limit(parsed: exp.Expression) -> int | None:
        limit_exp = parsed.find(exp.Limit)
        if not limit_exp or limit_exp.expression is None:
            return None
        expression = limit_exp.expression
        try:
            return int(expression.name)
        except (TypeError, ValueError):
            raise SQLValidationError("LIMIT must be a positive integer literal.") from None
