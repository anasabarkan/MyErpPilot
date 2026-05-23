"""
AI orchestration service for natural language business queries.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.orm import Session

from app.integrations.odoo import OdooRequestError
from app.orchestrator.intent_classifier import AIIntentClassifier
from app.schemas.ai import AIQueryIntent, AIQueryResponse
from app.services.ai_response_builder import build_ai_response
from app.services.llm_provider_service import any_provider_configured
from app.services.odoo_query_service import (
    OdooQueryExecutionError,
    OdooQueryService,
    OdooRuntimeContext,
)
from app.services.text_to_sql_service import TextToSQLCannotAnswerError, TextToSQLService
from app.core.config import settings


class AIUnsupportedQuestionError(RuntimeError):
    """Raised when the question cannot be mapped to a supported MVP handler."""


class AIIntentNotImplementedError(RuntimeError):
    """Raised when the intent is known but not implemented in current MVP."""


class AIOrchestratorService:
    _SUPPORTED_INTENTS = {
        AIQueryIntent.analytics_query,
        AIQueryIntent.summary_query,
    }
    _HANDLER_SUMMARIES: dict[str, str] = {
        "get_top_customers": "Here are your top {limit} customers based on total confirmed sales amount.",
        "get_top_products": "Here are your top {limit} products based on confirmed sales revenue.",
        "get_sales_summary": "Here is your sales summary from quotations and confirmed sales orders.",
        "get_invoice_status_summary": "Here is your invoice status summary from posted customer invoices.",
        "get_recent_orders": "Here are your {limit} most recent sales orders.",
    }
    _HANDLER_SUGGESTED_ACTIONS: dict[str, list[str]] = {
        "get_top_customers": [
            "Create a follow-up plan for high-value customers with declining order frequency.",
            "Review outstanding invoices for top customers to protect cash flow.",
        ],
        "get_top_products": [
            "Review stock coverage for top-selling products to avoid stock-outs.",
            "Assess pricing and margin trends on high-revenue products.",
        ],
        "get_sales_summary": [
            "Compare confirmed orders versus quotations to improve conversion rate.",
            "Break revenue down by month or sales team to identify performance trends.",
        ],
        "get_invoice_status_summary": [
            "Prioritize collection follow-ups for unpaid or overdue invoices.",
            "Investigate invoices in payment for potential reconciliation delays.",
        ],
        "get_recent_orders": [
            "Review recent orders for delivery bottlenecks.",
            "Flag unusual order values for manual validation.",
        ],
    }

    def __init__(self, db: Session) -> None:
        self.db = db
        self.intent_classifier = AIIntentClassifier()
        self.odoo_query_service = OdooQueryService(db)
        self.text_to_sql_service = TextToSQLService(db)

    def query(self, *, question: str, user_id: UUID) -> AIQueryResponse:
        normalized_question = question.strip()
        if not normalized_question:
            raise AIUnsupportedQuestionError("Question is empty.")

        if settings.TEXT2SQL_ENABLED and any_provider_configured():
            try:
                return self.text_to_sql_service.query(question=normalized_question, user_id=user_id)
            except TextToSQLCannotAnswerError as exc:
                raise AIUnsupportedQuestionError(str(exc)) from exc

        runtime = self.odoo_query_service.build_runtime_context()
        resolution = self.intent_classifier.classify(normalized_question)

        if resolution.intent not in self._SUPPORTED_INTENTS:
            raise AIIntentNotImplementedError(
                f"Intent '{resolution.intent.value}' is recognized but not implemented in this MVP."
            )

        if not resolution.handler:
            raise AIUnsupportedQuestionError(
                "This question is not supported yet. Try asking about top customers, products, sales summary, invoice status, or recent orders."
            )

        limit = self._extract_limit_from_question(normalized_question, default=5)
        handler = resolution.handler

        try:
            data = self._run_handler(handler=handler, runtime=runtime, limit=limit)
        except OdooQueryExecutionError:
            raise
        except OdooRequestError as exc:
            raise OdooQueryExecutionError(str(exc)) from exc

        summary = self._build_summary(handler=handler, limit=limit)
        suggested_actions = self._HANDLER_SUGGESTED_ACTIONS.get(handler, [])

        return build_ai_response(
            question=normalized_question,
            intent=resolution.intent,
            handler=handler,
            summary=summary,
            data=data,
            suggested_actions=suggested_actions,
        )

    def _build_summary(self, *, handler: str, limit: int) -> str:
        template = self._HANDLER_SUMMARIES.get(handler)
        if not template:
            return "Here is the result."
        return template.format(limit=limit)

    def _run_handler(
        self,
        *,
        handler: str,
        runtime: OdooRuntimeContext,
        limit: int,
    ) -> list[dict[str, object]] | dict[str, object]:
        if handler == "get_top_customers":
            return self.odoo_query_service.get_top_customers(runtime, limit=limit)
        if handler == "get_top_products":
            return self.odoo_query_service.get_top_products(runtime, limit=limit)
        if handler == "get_sales_summary":
            return self.odoo_query_service.get_sales_summary(runtime)
        if handler == "get_invoice_status_summary":
            return self.odoo_query_service.get_invoice_status_summary(runtime)
        if handler == "get_recent_orders":
            return self.odoo_query_service.get_recent_orders(runtime, limit=limit)
        raise AIUnsupportedQuestionError(
            "This question is not supported yet. Please rephrase with a supported analytics request."
        )

    @staticmethod
    def _extract_limit_from_question(question: str, *, default: int) -> int:
        patterns = (
            r"\btop\s+(\d{1,3})\b",
            r"\brecent\s+(\d{1,3})\b",
            r"\blast\s+(\d{1,3})\b",
        )
        lowered = question.lower()
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                value = int(match.group(1))
                return max(1, min(value, 50))
        return default
