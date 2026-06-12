"""
AI orchestration service for natural language business queries.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.orm import Session

from app.integrations.odoo import OdooRequestError, OdooConnectionError, OdooAuthenticationError
from app.orchestrator.intent_classifier import AIIntentClassifier
from app.schemas.ai import AIQueryIntent, AIQueryResponse
from app.services.ai_response_builder import build_ai_response
from app.services.llm_provider_service import any_provider_configured
from app.services.odoo_query_service import (
    OdooQueryExecutionError,
    OdooQueryService,
    OdooRuntimeContext,
    OdooQuerySourceNotConfiguredError,
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
            except TextToSQLCannotAnswerError:
                resolution = self.intent_classifier.classify(normalized_question)
                if resolution.intent == AIQueryIntent.dashboard_query:
                    raise AIIntentNotImplementedError(
                        "Intent 'dashboard_query' is recognized but not implemented in this MVP."
                    )
                if resolution.intent == AIQueryIntent.action_proposal:
                    raise AIIntentNotImplementedError(
                        "Intent 'action_proposal' is recognized but not implemented in this MVP."
                    )
                return self._handle_general_knowledge(question=normalized_question)
            except (OdooQuerySourceNotConfiguredError, OdooConnectionError, OdooAuthenticationError, OdooRequestError):
                resolution = self.intent_classifier.classify(normalized_question)
                if not resolution.handler:
                    return self._handle_general_knowledge(question=normalized_question)
                raise

        runtime = self.odoo_query_service.build_runtime_context()
        resolution = self.intent_classifier.classify(normalized_question)

        if resolution.intent not in self._SUPPORTED_INTENTS:
            raise AIIntentNotImplementedError(
                f"Intent '{resolution.intent.value}' is recognized but not implemented in this MVP."
            )

        if not resolution.handler:
            if settings.TEXT2SQL_ENABLED and any_provider_configured():
                return self._handle_general_knowledge(question=normalized_question)
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

    def _handle_general_knowledge(self, *, question: str) -> AIQueryResponse:
        system_prompt = (
            "You are MyERPPilot AI, a secure and intelligent enterprise AI copilot. "
            "The user is asking a general question, greeting, or business/technical concept question. "
            "Answer the user's question directly using clear, professional natural language. "
            "In addition, propose 2-3 relevant business or analytical actions they can perform (e.g. 'View top selling products', 'Check latest sales orders')."
        )
        user_prompt = f"User question: {question}"

        try:
            res = self.text_to_sql_service.provider_chain.generate_general(
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )
            summary = res.get("summary", "Hello! How can I assist you with your ERP data today?")
            suggested_actions = res.get("suggested_actions", [
                "Show top products",
                "List recent orders",
                "Show sales summary"
            ])
        except Exception:
            summary = "Hello! I am MyERPPilot AI, your secure enterprise AI copilot. How can I assist you with your ERP data, analytics, or workflow approvals today?"
            suggested_actions = [
                "Show top products",
                "List recent orders",
                "Show sales summary"
            ]

        return build_ai_response(
            question=question,
            intent=AIQueryIntent.general_query,
            handler="general_knowledge",
            summary=summary,
            data={},
            suggested_actions=suggested_actions,
        )
