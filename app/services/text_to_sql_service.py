"""
Text-to-SQL orchestration for Odoo analytics.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.schemas.ai import AIQueryIntent, AIQueryResponse
from app.services.ai_response_builder import build_ai_response
from app.services.ai_visualization_service import build_visualization_spec
from app.services.language_service import SupportedLanguage, detect_supported_language, language_name
from app.services.llm_provider_service import LLMProviderError, TextToSQLProviderChain
from app.services.odoo_query_service import OdooQueryExecutionError, OdooQueryService
from app.services.sql_execution_service import SQLExecutionError, SQLExecutionService
from app.services.sql_validation_service import SQLValidationError, SQLValidationService
from app.services.text_to_sql_schema_service import build_schema_prompt


_TEXT_TO_SQL_SUGGESTED_ACTIONS: dict[SupportedLanguage, list[str]] = {
    "en": [
        "Review the generated SQL in debug mode before using results for decisions.",
        "Refine the question with a date range or business filter for a narrower answer.",
    ],
    "fr": [
        "Verifiez le SQL genere en mode debug avant d'utiliser les resultats pour une decision.",
        "Precisez la question avec une periode ou un filtre metier pour obtenir une reponse plus ciblee.",
    ],
    "ar": [
        "راجع SQL الذي تم توليده في وضع التصحيح قبل استخدام النتائج لاتخاذ قرار.",
        "حدد السؤال بفترة زمنية أو مرشح عملي للحصول على إجابة أدق.",
    ],
}
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

    def query(
        self,
        *,
        question: str,
        user_id: UUID,
        detected_language: SupportedLanguage | None = None,
    ) -> AIQueryResponse:
        response_language = detected_language or detect_supported_language(question)
        runtime = self.odoo_query_service.build_runtime_context()
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            question=question,
            database_name=runtime.data_source.database_name,
            detected_language=response_language,
        )

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

        summary = draft.summary or "Here are the results from your Odoo data."
        visualization = build_visualization_spec(
            question=question,
            handler="text_to_sql",
            data=rows,
            summary=summary,
        )

        return build_ai_response(
            question=question,
            intent=AIQueryIntent.analytics_query,
            handler="text_to_sql",
            summary=summary,
            data=rows,
            suggested_actions=_TEXT_TO_SQL_SUGGESTED_ACTIONS[response_language],
            visualization=visualization,
            detected_language=response_language,
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
    def _build_user_prompt(*, question: str, database_name: str, detected_language: SupportedLanguage) -> str:
        return (
            f"Database: {database_name}\n"
            f"Max rows: {settings.TEXT2SQL_MAX_ROWS}\n"
            f"Business question: {question.strip()}\n"
            f"Response language: {language_name(detected_language)}.\n"
            "Generate a safe SQL query and a concise business summary in the response language."
        )
