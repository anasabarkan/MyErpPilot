"""
Text-to-SQL orchestration for Odoo analytics.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.schemas.ai import AIQueryIntent, AIQueryResponse
from app.services.ai_response_builder import build_ai_response
from app.services.llm_provider_service import LLMProviderError, TextToSQLProviderChain
from app.services.odoo_query_service import OdooQueryExecutionError, OdooQueryService
from app.services.sql_execution_service import SQLExecutionError, SQLExecutionService
from app.services.sql_validation_service import SQLValidationError, SQLValidationService
from app.services.text_to_sql_schema_service import build_schema_prompt


class TextToSQLCannotAnswerError(RuntimeError):
    """Raised when the LLM says the available schema cannot answer the question."""


class TextToSQLService:
    def __init__(
        self,
        db: Session,
        *,
        provider_chain: TextToSQLProviderChain | None = None,
        validator: SQLValidationService | None = None,
        executor: SQLExecutionService | None = None,
    ) -> None:
        self.db = db
        self.provider_chain = provider_chain or TextToSQLProviderChain()
        self.validator = validator or SQLValidationService(max_rows=settings.TEXT2SQL_MAX_ROWS)
        self.executor = executor or SQLExecutionService()
        self.odoo_query_service = OdooQueryService(db)

    def query(self, *, question: str, user_id: UUID) -> AIQueryResponse:
        runtime = self.odoo_query_service.build_runtime_context()
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(question=question, database_name=runtime.data_source.database_name)

        try:
            draft, fallback_used = self.provider_chain.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except LLMProviderError as exc:
            raise OdooQueryExecutionError("Text-to-SQL provider generation failed.") from exc

        if not draft.can_answer:
            raise TextToSQLCannotAnswerError(
                draft.summary or "The question cannot be answered from the allowed Odoo schema."
            )

        try:
            validated_sql = self.validator.validate(draft.sql)
        except SQLValidationError as exc:
            raise TextToSQLCannotAnswerError(str(exc)) from exc

        try:
            rows, execution_ms = self.executor.execute(validated_sql)
        except SQLExecutionError as exc:
            raise OdooQueryExecutionError("Text-to-SQL execution failed.") from exc

        return build_ai_response(
            question=question,
            intent=AIQueryIntent.analytics_query,
            handler="text_to_sql",
            summary=draft.summary or "Here are the results from your Odoo data.",
            data=rows,
            suggested_actions=[
                "Review the generated SQL in debug mode before using results for decisions.",
                "Refine the question with a date range or business filter for a narrower answer.",
            ],
            meta_overrides={
                "provider": draft.provider,
                "model": draft.model,
                "fallback_used": fallback_used,
                "generated_sql": validated_sql.sql,
                "execution_ms": execution_ms,
                "row_count": len(rows),
            },
        )

    @staticmethod
    def _build_system_prompt() -> str:
        return "\n\n".join(
            [
                "You are a careful Text-to-SQL assistant for Odoo ERP analytics.",
                "Return only JSON matching the required schema.",
                "If the question asks to modify data or cannot be answered with the allowed schema, set can_answer=false and sql=''.",
                build_schema_prompt(),
            ]
        )

    @staticmethod
    def _build_user_prompt(*, question: str, database_name: str) -> str:
        return (
            f"Database: {database_name}\n"
            f"Max rows: {settings.TEXT2SQL_MAX_ROWS}\n"
            f"Business question: {question.strip()}\n"
            "Generate a safe SQL query and a concise business summary."
        )
