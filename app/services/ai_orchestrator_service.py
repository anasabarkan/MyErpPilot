"""
AI orchestration service for natural language business queries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.integrations.odoo import OdooRequestError, OdooConnectionError, OdooAuthenticationError
from app.orchestrator.intent_classifier import AIIntentClassifier
from app.schemas.ai import AIQueryIntent, AIQueryResponse, AIVisualizationSpec
from app.services.ai_response_builder import build_ai_response
from app.services.ai_visualization_service import build_visualization_spec
from app.services.llm_provider_service import any_provider_configured
from app.services.odoo_query_service import (
    OdooQueryExecutionError,
    OdooQueryService,
    OdooRuntimeContext,
    OdooQuerySourceNotConfiguredError,
)
from app.services.product_prediction_service import (
    ProductPredictionDependencyError,
    ProductPredictionInsufficientDataError,
    ProductPredictionModelLoadError,
    ProductPredictionModelMissingError,
    ProductPredictionService,
)
from app.services.text_to_sql_service import TextToSQLCannotAnswerError, TextToSQLService
from app.core.config import settings


class AIUnsupportedQuestionError(RuntimeError):
    """Raised when the question cannot be mapped to a supported MVP handler."""


class AIIntentNotImplementedError(RuntimeError):
    """Raised when the intent is known but not implemented in current MVP."""


@dataclass(frozen=True)
class _DateRange:
    date_from: str
    date_to: str
    label: str


class AIOrchestratorService:
    _SUPPORTED_INTENTS = {
        AIQueryIntent.analytics_query,
        AIQueryIntent.prediction_query,
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
        self.product_prediction_service = ProductPredictionService()
        self.text_to_sql_service = TextToSQLService(db)

    def query(self, *, question: str, user_id: UUID) -> AIQueryResponse:
        normalized_question = question.strip()
        if not normalized_question:
            raise AIUnsupportedQuestionError("Question is empty.")

        initial_resolution = self.intent_classifier.classify(normalized_question)
        if initial_resolution.intent == AIQueryIntent.prediction_query:
            return self._handle_prediction_query(question=normalized_question)

        if settings.TEXT2SQL_ENABLED and any_provider_configured() and not initial_resolution.handler:
            try:
                return self.text_to_sql_service.query(question=normalized_question, user_id=user_id)
            except TextToSQLCannotAnswerError:
                if initial_resolution.intent == AIQueryIntent.dashboard_query:
                    raise AIIntentNotImplementedError(
                        "Intent 'dashboard_query' is recognized but not implemented in this MVP."
                    )
                if initial_resolution.intent == AIQueryIntent.action_proposal:
                    raise AIIntentNotImplementedError(
                        "Intent 'action_proposal' is recognized but not implemented in this MVP."
                    )
                return self._handle_general_knowledge(question=normalized_question)
            except (OdooQuerySourceNotConfiguredError, OdooConnectionError, OdooAuthenticationError, OdooRequestError):
                if not initial_resolution.handler:
                    return self._handle_general_knowledge(question=normalized_question)
                raise

        runtime = self.odoo_query_service.build_runtime_context()
        resolution = initial_resolution

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
        date_range = self._extract_date_range_from_question(normalized_question)

        try:
            data = self._run_handler(handler=handler, runtime=runtime, limit=limit, date_range=date_range)
        except OdooQueryExecutionError:
            raise
        except OdooRequestError as exc:
            raise OdooQueryExecutionError(str(exc)) from exc

        summary = self._build_summary(handler=handler, limit=limit, date_range=date_range)
        suggested_actions = self._HANDLER_SUGGESTED_ACTIONS.get(handler, [])
        visualization = build_visualization_spec(
            question=normalized_question,
            handler=handler,
            data=data,
            summary=summary,
        )

        return build_ai_response(
            question=normalized_question,
            intent=resolution.intent,
            handler=handler,
            summary=summary,
            data=data,
            suggested_actions=suggested_actions,
            visualization=visualization,
        )

    def _handle_prediction_query(self, *, question: str) -> AIQueryResponse:
        target_year = self._extract_prediction_target_year(question)

        try:
            prediction = self.product_prediction_service.predict_best_product(
                target_year=target_year,
                limit=5,
            )
        except (
            ProductPredictionDependencyError,
            ProductPredictionInsufficientDataError,
            ProductPredictionModelLoadError,
            ProductPredictionModelMissingError,
        ) as exc:
            return self._build_prediction_unavailable_response(
                question=question,
                target_year=target_year,
                reason=str(exc),
            )

        best_product = prediction.best_product
        if prediction.data_quality == "limited_data":
            summary = (
                f"Limited-data estimate: {best_product.product_name} is currently the strongest product for {target_year}, "
                f"with estimated revenue of {best_product.predicted_revenue:,.2f} "
                f"and {best_product.confidence * 100:.1f}% confidence."
            )
        elif prediction.data_quality == "short_history":
            summary = (
                f"Short-history forecast: {best_product.product_name} is the strongest product for {target_year}, "
                f"with projected revenue of {best_product.predicted_revenue:,.2f} "
                f"and {best_product.confidence * 100:.1f}% confidence."
            )
        else:
            summary = (
                f"I predict {best_product.product_name} is the strongest product for {target_year}, "
                f"with projected revenue of {best_product.predicted_revenue:,.2f} "
                f"and {best_product.confidence * 100:.1f}% confidence."
            )
        if prediction.warning:
            summary = f"{summary} {prediction.warning}"
        chart_data = [
            {
                "product_name": candidate.product_name,
                "predicted_revenue": candidate.predicted_revenue,
                "confidence": round(candidate.confidence * 100, 2),
            }
            for candidate in prediction.candidates
        ]
        visualization = AIVisualizationSpec(
            type="bar",
            title=f"Predicted top products for {target_year}",
            data=chart_data,
            x_key="product_name",
            y_keys=["predicted_revenue"],
            x_label="Product",
            y_label="Predicted Revenue",
            description="Forecasted from monthly product revenue trends.",
        )

        return build_ai_response(
            question=question,
            intent=AIQueryIntent.prediction_query,
            handler="predict_best_product",
            summary=summary,
            data=prediction.model_dump(mode="json"),
            suggested_actions=[
                f"Review stock coverage for {best_product.product_name} before {target_year}.",
                "Retrain the model after each sales cycle to improve prediction quality.",
                "Compare the forecast against supplier lead times and margins.",
            ],
            visualization=visualization,
            prediction=prediction,
        )

    def _build_prediction_unavailable_response(
        self,
        *,
        question: str,
        target_year: int,
        reason: str,
    ) -> AIQueryResponse:
        return build_ai_response(
            question=question,
            intent=AIQueryIntent.prediction_query,
            handler="predict_best_product",
            summary=(
                f"I cannot make a reliable product prediction for {target_year} yet. "
                f"{reason}"
            ),
            data={
                "status": "prediction_unavailable",
                "target_period": str(target_year),
                "reason": reason,
            },
            suggested_actions=[
                "Run python -m app.scripts.train_product_prediction_model to generate the model artifact.",
                "Make sure Odoo has at least one confirmed product sale with revenue before training.",
                "Ask for top selling products this year to verify the source data first.",
            ],
        )

    def _build_summary(self, *, handler: str, limit: int, date_range: _DateRange | None = None) -> str:
        template = self._HANDLER_SUMMARIES.get(handler)
        if not template:
            summary = "Here is the result."
        else:
            summary = template.format(limit=limit)
        if date_range:
            return f"{summary.rstrip('.')} for {date_range.label}."
        return summary

    def _run_handler(
        self,
        *,
        handler: str,
        runtime: OdooRuntimeContext,
        limit: int,
        date_range: _DateRange | None,
    ) -> list[dict[str, object]] | dict[str, object]:
        date_kwargs = {}
        if date_range:
            date_kwargs = {"date_from": date_range.date_from, "date_to": date_range.date_to}

        if handler == "get_top_customers":
            return self.odoo_query_service.get_top_customers(runtime, limit=limit, **date_kwargs)
        if handler == "get_top_products":
            return self.odoo_query_service.get_top_products(runtime, limit=limit, **date_kwargs)
        if handler == "get_sales_summary":
            return self.odoo_query_service.get_sales_summary(runtime, **date_kwargs)
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

    @staticmethod
    def _extract_date_range_from_question(question: str) -> _DateRange | None:
        current_year = datetime.now(timezone.utc).year

        if any(phrase in question for phrase in ("this year", "current year", "cette annee", "cette année")):
            return _DateRange(
                date_from=f"{current_year}-01-01 00:00:00",
                date_to=f"{current_year}-12-31 23:59:59",
                label=str(current_year),
            )

        if "last year" in question or "previous year" in question:
            year = current_year - 1
            return _DateRange(
                date_from=f"{year}-01-01 00:00:00",
                date_to=f"{year}-12-31 23:59:59",
                label=str(year),
            )

        if "this month" in question or "current month" in question:
            now = datetime.now(timezone.utc)
            next_month_year = now.year + (1 if now.month == 12 else 0)
            next_month = 1 if now.month == 12 else now.month + 1
            return _DateRange(
                date_from=f"{now.year}-{now.month:02d}-01 00:00:00",
                date_to=f"{next_month_year}-{next_month:02d}-01 00:00:00",
                label=now.strftime("%B %Y"),
            )

        return None

    @staticmethod
    def _extract_prediction_target_year(question: str) -> int:
        current_year = datetime.now(timezone.utc).year
        explicit_year = re.search(r"\b(20\d{2})\b", question)
        if explicit_year:
            return int(explicit_year.group(1))
        if "this year" in question or "current year" in question:
            return current_year
        return current_year + 1

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
