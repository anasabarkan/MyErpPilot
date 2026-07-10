"""
Deterministic NLP intent and handler mapping for AI query orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from app.schemas.ai import AIQueryIntent


@dataclass(frozen=True)
class IntentResolution:
    intent: AIQueryIntent
    handler: str | None
    mapped_entities: dict[str, str]


class AIIntentClassifier:
    """
    Rule-based intent classifier for MVP.

    This module is intentionally deterministic so it can be replaced later by an
    LLM-based classifier without changing the orchestrator contract.
    """

    _ACTION_PROPOSAL_KEYWORDS = (
        "action proposal",
        "what should i do",
        "what do you recommend",
        "recommend next action",
        "next best action",
    )
    _PREDICTION_KEYWORDS = (
        "forecast",
        "forcast",
        "predic",
        "predict",
        "predicted",
        "prediction",
        "next year",
        "next month",
        "future sales",
        "best product for next",
        "best product next",
    )
    _DASHBOARD_KEYWORDS = ("dashboard", "kpi", "overview", "board view")
    _SUMMARY_KEYWORDS = ("summary", "summarize", "resume", "résumé", "recap", "overview of")

    _HANDLER_KEYWORDS: dict[str, tuple[str, ...]] = {
        "predict_best_product": (
            "predic best product",
            "predic the best product",
            "predict best product",
            "predict the best product",
            "forcast best product",
            "forecast best product",
            "best product next year",
            "best selling product next year",
            "best product for next year",
        ),
        "get_top_customers": (
            "top customers",
            "meilleurs clients",
            "top clients",
            "أفضل العملاء",
            "best customers",
            "biggest customers",
            "customer sales",
            "sales by customer",
        ),
        "get_top_products": (
            "best selling products",
            "meilleurs produits",
            "produits les plus vendus",
            "أفضل المنتجات",
            "selling products",
            "sold products",
            "top products",
            "top selling products",
            "best products",
            "product sales",
            "sales by product",
        ),
        "get_sales_summary": ("sales summary", "resume des ventes", "résumé des ventes", "revenue summary", "sales overview", "sales chart", "sales graph"),
        "get_invoice_status_summary": ("invoice status", "invoices status", "invoice payment status"),
        "get_recent_orders": ("recent orders", "commandes recentes", "commandes récentes", "latest orders", "last orders"),
    }
    _HANDLER_PATTERNS: dict[str, tuple[str, ...]] = {
        "predict_best_product": (
            r"\b(predic|predict|forecast|forcast|prediction)\b.*\bproducts?\b",
            r"\bbest\s+products?\b.*\bnext\s+(year|month)\b",
        ),
        "get_top_customers": (r"\btop\s+\d+\s+customers?\b",),
        "get_top_products": (r"\btop\s+\d+\s+products?\b", r"\b(graph|graphe|chart|diagramme?)\b.*\bproducts?\b"),
        "get_recent_orders": (r"\brecent\s+\d+\s+orders?\b", r"\blast\s+\d+\s+orders?\b"),
    }

    _ENTITY_KEYWORDS: dict[str, tuple[str, ...]] = {
        "res.partner": ("customer", "customers", "client", "clients", "partner", "partners"),
        "product.product": ("product", "products", "item", "items", "sku"),
        "sale.order": ("sale", "sales", "order", "orders", "quotation", "quotations"),
        "account.move": ("invoice", "invoices", "billing", "payment status"),
    }

    def classify(self, question: str) -> IntentResolution:
        normalized = self._normalize(question)
        handler = self._resolve_handler(normalized)
        intent = self._resolve_intent(normalized, handler=handler)
        mapped_entities = self._map_business_terms(normalized)
        return IntentResolution(intent=intent, handler=handler, mapped_entities=mapped_entities)

    @staticmethod
    def _normalize(question: str) -> str:
        return " ".join(question.lower().split())

    def _resolve_intent(self, normalized_question: str, *, handler: str | None) -> AIQueryIntent:
        if handler == "predict_best_product" or any(keyword in normalized_question for keyword in self._PREDICTION_KEYWORDS):
            return AIQueryIntent.prediction_query

        if any(keyword in normalized_question for keyword in self._ACTION_PROPOSAL_KEYWORDS):
            return AIQueryIntent.action_proposal

        if any(keyword in normalized_question for keyword in self._DASHBOARD_KEYWORDS):
            return AIQueryIntent.dashboard_query

        if handler == "get_sales_summary":
            return AIQueryIntent.summary_query

        if any(keyword in normalized_question for keyword in self._SUMMARY_KEYWORDS):
            return AIQueryIntent.summary_query

        return AIQueryIntent.analytics_query

    def _resolve_handler(self, normalized_question: str) -> str | None:
        for handler, keywords in self._HANDLER_KEYWORDS.items():
            if any(keyword in normalized_question for keyword in keywords):
                return handler
            patterns = self._HANDLER_PATTERNS.get(handler, ())
            if any(re.search(pattern, normalized_question) for pattern in patterns):
                return handler

        # Friendly default for broad "summary" prompts in MVP.
        if any(keyword in normalized_question for keyword in self._SUMMARY_KEYWORDS):
            return "get_sales_summary"
        return None

    def _map_business_terms(self, normalized_question: str) -> dict[str, str]:
        entities: dict[str, str] = {}
        for model_name, keywords in self._ENTITY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in normalized_question:
                    entities[keyword] = model_name
        return entities
