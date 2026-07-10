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
from app.services.language_service import SupportedLanguage, detect_supported_language, language_name
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



_HANDLER_SUMMARIES_I18N: dict[SupportedLanguage, dict[str, str]] = {
    "en": {
        "get_top_customers": "Here are your top {limit} customers based on total confirmed sales amount.",
        "get_top_products": "Here are your top {limit} products based on confirmed sales revenue.",
        "get_sales_summary": "Here is your sales summary from quotations and confirmed sales orders.",
        "get_invoice_status_summary": "Here is your invoice status summary from posted customer invoices.",
        "get_recent_orders": "Here are your {limit} most recent sales orders.",
    },
    "fr": {
        "get_top_customers": "Voici vos {limit} meilleurs clients selon le montant total des ventes confirmees.",
        "get_top_products": "Voici vos {limit} meilleurs produits selon le chiffre d'affaires confirme.",
        "get_sales_summary": "Voici le resume de vos ventes a partir des devis et commandes confirmees.",
        "get_invoice_status_summary": "Voici le resume du statut de vos factures clients comptabilisees.",
        "get_recent_orders": "Voici vos {limit} commandes de vente les plus recentes.",
    },
    "ar": {
        "get_top_customers": "إليك أفضل {limit} عملاء حسب إجمالي المبيعات المؤكدة.",
        "get_top_products": "إليك أفضل {limit} منتجات حسب الإيرادات المؤكدة.",
        "get_sales_summary": "إليك ملخص المبيعات من عروض الأسعار وأوامر البيع المؤكدة.",
        "get_invoice_status_summary": "إليك ملخص حالة فواتير العملاء المرحلة.",
        "get_recent_orders": "إليك أحدث {limit} أوامر بيع.",
    },
}

_HANDLER_SUGGESTED_ACTIONS_I18N: dict[SupportedLanguage, dict[str, list[str]]] = {
    "en": {
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
    },
    "fr": {
        "get_top_customers": [
            "Preparez un plan de suivi pour les clients a forte valeur dont la frequence d'achat baisse.",
            "Verifiez les factures impayees des meilleurs clients pour proteger la tresorerie.",
        ],
        "get_top_products": [
            "Verifiez la couverture de stock des produits les plus vendus pour eviter les ruptures.",
            "Analysez les tendances de prix et de marge des produits a fort chiffre d'affaires.",
        ],
        "get_sales_summary": [
            "Comparez les commandes confirmees aux devis pour ameliorer le taux de conversion.",
            "Decomposez le chiffre d'affaires par mois ou equipe commerciale pour identifier les tendances.",
        ],
        "get_invoice_status_summary": [
            "Priorisez les relances de recouvrement pour les factures impayees ou en retard.",
            "Analysez les factures en paiement pour detecter d'eventuels retards de rapprochement.",
        ],
        "get_recent_orders": [
            "Verifiez les commandes recentes pour detecter les blocages de livraison.",
            "Signalez les montants inhabituels pour une validation manuelle.",
        ],
    },
    "ar": {
        "get_top_customers": [
            "أنشئ خطة متابعة للعملاء ذوي القيمة العالية الذين انخفضت وتيرة طلباتهم.",
            "راجع الفواتير المستحقة لأفضل العملاء لحماية التدفق النقدي.",
        ],
        "get_top_products": [
            "راجع تغطية المخزون للمنتجات الأكثر مبيعا لتجنب نفاد المخزون.",
            "حلل اتجاهات الأسعار والهامش للمنتجات ذات الإيرادات العالية.",
        ],
        "get_sales_summary": [
            "قارن الطلبات المؤكدة بعروض الأسعار لتحسين معدل التحويل.",
            "قسم الإيرادات حسب الشهر أو فريق المبيعات لتحديد اتجاهات الأداء.",
        ],
        "get_invoice_status_summary": [
            "امنح أولوية لمتابعة التحصيل للفواتير غير المدفوعة أو المتأخرة.",
            "حقق في الفواتير قيد الدفع لاكتشاف أي تأخير محتمل في المطابقة.",
        ],
        "get_recent_orders": [
            "راجع الطلبات الحديثة لاكتشاف عوائق التسليم.",
            "ضع علامة على قيم الطلبات غير المعتادة للمراجعة اليدوية.",
        ],
    },
}

_GENERAL_FALLBACK_I18N: dict[SupportedLanguage, tuple[str, list[str]]] = {
    "en": (
        "Hello! I am MyERPPilot AI, your secure enterprise AI copilot. How can I assist you with your ERP data, analytics, or workflow approvals today?",
        ["Show top products", "List recent orders", "Show sales summary"],
    ),
    "fr": (
        "Bonjour ! Je suis MyERPPilot AI, votre copilote IA d'entreprise securise. Comment puis-je vous aider avec vos donnees ERP, vos analyses ou vos validations aujourd'hui ?",
        ["Afficher les meilleurs produits", "Lister les commandes recentes", "Afficher le resume des ventes"],
    ),
    "ar": (
        "مرحبا! أنا MyERPPilot AI، مساعدك الذكي الآمن للمؤسسة. كيف يمكنني مساعدتك اليوم في بيانات ERP أو التحليلات أو موافقات سير العمل؟",
        ["اعرض أفضل المنتجات", "اعرض أحدث الطلبات", "اعرض ملخص المبيعات"],
    ),
}

_PREDICTION_UNAVAILABLE_ACTIONS_I18N: dict[SupportedLanguage, list[str]] = {
    "en": [
        "Run python -m app.scripts.train_product_prediction_model to generate the model artifact.",
        "Make sure Odoo has at least one confirmed product sale with revenue before training.",
        "Ask for top selling products this year to verify the source data first.",
    ],
    "fr": [
        "Executez python -m app.scripts.train_product_prediction_model pour generer l'artefact du modele.",
        "Verifiez qu'Odoo contient au moins une vente produit confirmee avec revenu avant l'entrainement.",
        "Demandez les meilleurs produits vendus cette annee pour verifier d'abord les donnees source.",
    ],
    "ar": [
        "شغل python -m app.scripts.train_product_prediction_model لإنشاء ملف النموذج.",
        "تأكد من أن Odoo يحتوي على عملية بيع منتج مؤكدة واحدة على الأقل مع إيراد قبل التدريب.",
        "اطلب أفضل المنتجات مبيعا هذا العام للتحقق من البيانات المصدر أولا.",
    ],
}




def _localized_empty_question(language: SupportedLanguage) -> str:
    return {
        "en": "Question is empty.",
        "fr": "La question est vide.",
        "ar": "السؤال فارغ.",
    }[language]


def _localized_unsupported_question(language: SupportedLanguage) -> str:
    return {
        "en": "This question is not supported yet. Try asking about top customers, products, sales summary, invoice status, or recent orders.",
        "fr": "Cette question n'est pas encore prise en charge. Essayez de demander les meilleurs clients, les produits, le resume des ventes, le statut des factures ou les commandes recentes.",
        "ar": "هذا السؤال غير مدعوم بعد. جرب السؤال عن أفضل العملاء أو المنتجات أو ملخص المبيعات أو حالة الفواتير أو أحدث الطلبات.",
    }[language]


def _localized_result_fallback(language: SupportedLanguage) -> str:
    return {
        "en": "Here is the result.",
        "fr": "Voici le resultat.",
        "ar": "إليك النتيجة.",
    }[language]


def _localized_prediction_summary(
    *,
    language: SupportedLanguage,
    quality: str | None,
    product_name: str,
    target_year: int,
    predicted_revenue: float,
    confidence: float,
) -> str:
    confidence_percent = confidence * 100
    if language == "fr":
        prefix = "Estimation avec donnees limitees" if quality == "limited_data" else "Prevision avec historique court" if quality == "short_history" else "Je prevois"
        return (
            f"{prefix} : {product_name} est le produit le plus prometteur pour {target_year}, "
            f"avec un chiffre d'affaires estime de {predicted_revenue:,.2f} "
            f"et une confiance de {confidence_percent:.1f} %."
        )
    if language == "ar":
        prefix = "تقدير ببيانات محدودة" if quality == "limited_data" else "توقع بسجل قصير" if quality == "short_history" else "أتوقع"
        return (
            f"{prefix}: {product_name} هو أقوى منتج لسنة {target_year}، "
            f"مع إيراد متوقع قدره {predicted_revenue:,.2f} "
            f"ودرجة ثقة {confidence_percent:.1f}%."
        )
    if quality == "limited_data":
        return (
            f"Limited-data estimate: {product_name} is currently the strongest product for {target_year}, "
            f"with estimated revenue of {predicted_revenue:,.2f} "
            f"and {confidence_percent:.1f}% confidence."
        )
    if quality == "short_history":
        return (
            f"Short-history forecast: {product_name} is the strongest product for {target_year}, "
            f"with projected revenue of {predicted_revenue:,.2f} "
            f"and {confidence_percent:.1f}% confidence."
        )
    return (
        f"I predict {product_name} is the strongest product for {target_year}, "
        f"with projected revenue of {predicted_revenue:,.2f} "
        f"and {confidence_percent:.1f}% confidence."
    )


def _localized_prediction_actions(language: SupportedLanguage, product_name: str, target_year: int) -> list[str]:
    if language == "fr":
        return [
            f"Verifiez la couverture de stock de {product_name} avant {target_year}.",
            "Reentrainez le modele apres chaque cycle de vente pour ameliorer la qualite des previsions.",
            "Comparez la prevision aux delais fournisseurs et aux marges.",
        ]
    if language == "ar":
        return [
            f"راجع تغطية مخزون {product_name} قبل {target_year}.",
            "أعد تدريب النموذج بعد كل دورة مبيعات لتحسين جودة التوقع.",
            "قارن التوقع بمهل الموردين والهوامش.",
        ]
    return [
        f"Review stock coverage for {product_name} before {target_year}.",
        "Retrain the model after each sales cycle to improve prediction quality.",
        "Compare the forecast against supplier lead times and margins.",
    ]


def _localized_prediction_unavailable(language: SupportedLanguage, target_year: int, reason: str) -> str:
    if language == "fr":
        return f"Je ne peux pas encore etablir une prediction fiable des produits pour {target_year}. {reason}"
    if language == "ar":
        return f"لا يمكنني إنشاء توقع موثوق للمنتجات لسنة {target_year} بعد. {reason}"
    return f"I cannot make a reliable product prediction for {target_year} yet. {reason}"

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
        response_language = detect_supported_language(normalized_question)
        if not normalized_question:
            raise AIUnsupportedQuestionError(_localized_empty_question(response_language))

        initial_resolution = self.intent_classifier.classify(normalized_question)
        if initial_resolution.intent == AIQueryIntent.prediction_query:
            return self._handle_prediction_query(question=normalized_question, detected_language=response_language)

        if settings.TEXT2SQL_ENABLED and any_provider_configured() and not initial_resolution.handler:
            try:
                return self.text_to_sql_service.query(
                    question=normalized_question,
                    user_id=user_id,
                    detected_language=response_language,
                )
            except TextToSQLCannotAnswerError:
                if initial_resolution.intent == AIQueryIntent.dashboard_query:
                    raise AIIntentNotImplementedError(
                        "Intent 'dashboard_query' is recognized but not implemented in this MVP."
                    )
                if initial_resolution.intent == AIQueryIntent.action_proposal:
                    raise AIIntentNotImplementedError(
                        "Intent 'action_proposal' is recognized but not implemented in this MVP."
                    )
                return self._handle_general_knowledge(question=normalized_question, detected_language=response_language)
            except (OdooQuerySourceNotConfiguredError, OdooConnectionError, OdooAuthenticationError, OdooRequestError):
                if not initial_resolution.handler:
                    return self._handle_general_knowledge(question=normalized_question, detected_language=response_language)
                raise

        runtime = self.odoo_query_service.build_runtime_context()
        resolution = initial_resolution

        if resolution.intent not in self._SUPPORTED_INTENTS:
            raise AIIntentNotImplementedError(
                f"Intent '{resolution.intent.value}' is recognized but not implemented in this MVP."
            )

        if not resolution.handler:
            if settings.TEXT2SQL_ENABLED and any_provider_configured():
                return self._handle_general_knowledge(question=normalized_question, detected_language=response_language)
            raise AIUnsupportedQuestionError(_localized_unsupported_question(response_language))

        limit = self._extract_limit_from_question(normalized_question, default=5)
        handler = resolution.handler
        date_range = self._extract_date_range_from_question(normalized_question)

        try:
            data = self._run_handler(handler=handler, runtime=runtime, limit=limit, date_range=date_range)
        except OdooQueryExecutionError:
            raise
        except OdooRequestError as exc:
            raise OdooQueryExecutionError(str(exc)) from exc

        summary = self._build_summary(
            handler=handler,
            limit=limit,
            date_range=date_range,
            detected_language=response_language,
        )
        suggested_actions = _HANDLER_SUGGESTED_ACTIONS_I18N[response_language].get(handler, [])
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
            detected_language=response_language,
        )

    def _handle_prediction_query(
        self,
        *,
        question: str,
        detected_language: SupportedLanguage,
    ) -> AIQueryResponse:
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
                detected_language=detected_language,
            )

        best_product = prediction.best_product
        summary = _localized_prediction_summary(
            language=detected_language,
            quality=prediction.data_quality,
            product_name=best_product.product_name,
            target_year=target_year,
            predicted_revenue=best_product.predicted_revenue,
            confidence=best_product.confidence,
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
            suggested_actions=_localized_prediction_actions(
                detected_language,
                best_product.product_name,
                target_year,
            ),
            visualization=visualization,
            prediction=prediction,
            detected_language=detected_language,
        )

    def _build_prediction_unavailable_response(
        self,
        *,
        question: str,
        target_year: int,
        reason: str,
        detected_language: SupportedLanguage,
    ) -> AIQueryResponse:
        return build_ai_response(
            question=question,
            intent=AIQueryIntent.prediction_query,
            handler="predict_best_product",
            summary=_localized_prediction_unavailable(detected_language, target_year, reason),
            data={
                "status": "prediction_unavailable",
                "target_period": str(target_year),
                "reason": reason,
            },
            suggested_actions=_PREDICTION_UNAVAILABLE_ACTIONS_I18N[detected_language],
            detected_language=detected_language,
        )

    def _build_summary(
        self,
        *,
        handler: str,
        limit: int,
        detected_language: SupportedLanguage,
        date_range: _DateRange | None = None,
    ) -> str:
        template = _HANDLER_SUMMARIES_I18N[detected_language].get(handler)
        summary = template.format(limit=limit) if template else _localized_result_fallback(detected_language)
        if date_range:
            if detected_language == "fr":
                return f"{summary.rstrip('.')} pour {date_range.label}."
            if detected_language == "ar":
                return f"{summary.rstrip('.')} للفترة {date_range.label}."
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

        if any(phrase in question for phrase in ("this year", "current year", "cette annee", "cette annÃ©e")):
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

    def _handle_general_knowledge(
        self,
        *,
        question: str,
        detected_language: SupportedLanguage,
    ) -> AIQueryResponse:
        system_prompt = (
            "You are MyERPPilot AI, a secure and intelligent enterprise AI copilot. "
            "The user is asking a general question, greeting, or business/technical concept question. "
            "Answer the user's question directly using clear, professional natural language. "
            f"Respond in {language_name(detected_language)}. "
            "In addition, propose 2-3 relevant business or analytical actions they can perform "
            "in the same response language."
        )
        user_prompt = f"User question: {question}"
        fallback_summary, fallback_actions = _GENERAL_FALLBACK_I18N[detected_language]

        try:
            res = self.text_to_sql_service.provider_chain.generate_general(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                detected_language=detected_language,
            )
            summary = res.get("summary", fallback_summary)
            suggested_actions = res.get("suggested_actions", fallback_actions)
        except Exception:
            summary = fallback_summary
            suggested_actions = fallback_actions

        return build_ai_response(
            question=question,
            intent=AIQueryIntent.general_query,
            handler="general_knowledge",
            summary=summary,
            data={},
            suggested_actions=suggested_actions,
            detected_language=detected_language,
        )
