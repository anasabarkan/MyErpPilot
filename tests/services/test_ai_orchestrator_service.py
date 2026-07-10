from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.schemas.ai import AIQueryIntent
from app.services.ai_orchestrator_service import (
    AIIntentNotImplementedError,
    AIOrchestratorService,
    AIUnsupportedQuestionError,
)
from app.services.odoo_query_service import OdooQueryExecutionError
from app.services.product_prediction_service import ProductPredictionService, train_product_prediction_model


@pytest.fixture(autouse=True)
def disable_text2sql(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "TEXT2SQL_ENABLED", False)


@dataclass
class _FakeRuntime:
    source_name: str = "odoo"


class _FakeOdooQueryService:
    def __init__(self) -> None:
        self.last_limit = None
        self.last_date_from = None
        self.last_date_to = None

    def build_runtime_context(self) -> _FakeRuntime:
        return _FakeRuntime()

    def get_top_customers(self, runtime: _FakeRuntime, *, limit: int = 5, date_from=None, date_to=None):
        self.last_limit = limit
        self.last_date_from = date_from
        self.last_date_to = date_to
        return [{"customer_id": 1, "customer_name": "Agrolait", "sales_amount": 1500.0}]

    def get_top_products(self, runtime: _FakeRuntime, *, limit: int = 5, date_from=None, date_to=None):
        self.last_limit = limit
        self.last_date_from = date_from
        self.last_date_to = date_to
        return [{"product_id": 2, "product_name": "Desk", "quantity_sold": 4.0, "revenue": 900.0}]

    def get_sales_summary(self, runtime: _FakeRuntime, *, date_from=None, date_to=None):
        self.last_date_from = date_from
        self.last_date_to = date_to
        return {"revenue_total": 10000.0}

    def get_invoice_status_summary(self, runtime: _FakeRuntime):
        return {"paid_invoices": 8, "unpaid_invoices": 2}

    def get_recent_orders(self, runtime: _FakeRuntime, *, limit: int = 10):
        self.last_limit = limit
        return [{"id": 10, "name": "SO010"}]

    def get_product_sales_history(self, runtime: _FakeRuntime, *, months: int = 36):
        history = []
        for index in range(12):
            month = f"2025-{index + 1:02d}"
            history.append(
                {
                    "product_id": 2,
                    "product_name": "Desk",
                    "month": month,
                    "revenue": 1000.0 + index * 120.0,
                    "quantity_sold": 5.0 + index,
                }
            )
            history.append(
                {
                    "product_id": 3,
                    "product_name": "Chair",
                    "month": month,
                    "revenue": 1200.0,
                    "quantity_sold": 8.0,
                }
            )
        return history


def _fake_prediction_history() -> list[dict[str, object]]:
    history: list[dict[str, object]] = []
    start_year = datetime.now(timezone.utc).year - 1
    for index in range(24):
        year = start_year + index // 12
        month = index % 12 + 1
        month_key = f"{year}-{month:02d}"
        history.append(
            {
                "product_id": 2,
                "product_name": "Desk",
                "month": month_key,
                "revenue": 1500.0 + index * 160.0,
                "quantity_sold": 8.0 + index,
            }
        )
        history.append(
            {
                "product_id": 3,
                "product_name": "Chair",
                "month": month_key,
                "revenue": 450.0 + index * 5.0,
                "quantity_sold": 4.0,
            }
        )
    return history


def _attach_temp_prediction_model(service: AIOrchestratorService, artifact_tmp_path):
    model_path = artifact_tmp_path / "product_prediction_model.pkl"
    train_product_prediction_model(
        history=_fake_prediction_history(),
        model_path=model_path,
        metadata_path=artifact_tmp_path / "product_prediction_model.meta.json",
    )
    service.product_prediction_service = ProductPredictionService(model_path=model_path)


def test_orchestrator_routes_top_customers_and_parses_limit():
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    fake_odoo = _FakeOdooQueryService()
    service.odoo_query_service = fake_odoo  # type: ignore[assignment]

    result = service.query(question="Who are my top 3 customers?", user_id=uuid4())

    assert result.intent == AIQueryIntent.analytics_query
    assert result.handler == "get_top_customers"
    assert fake_odoo.last_limit == 3
    assert result.data[0]["customer_name"] == "Agrolait"




def test_orchestrator_localizes_french_deterministic_analytics_query():
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    fake_odoo = _FakeOdooQueryService()
    service.odoo_query_service = fake_odoo  # type: ignore[assignment]

    result = service.query(question="Quels sont mes meilleurs clients ?", user_id=uuid4())

    assert result.intent == AIQueryIntent.analytics_query
    assert result.handler == "get_top_customers"
    assert result.meta.detected_language == "fr"
    assert "Voici" in result.summary
    assert "clients" in result.summary
    assert result.suggested_actions
    assert "Verifiez" in result.suggested_actions[1]

def test_orchestrator_returns_summary_intent_for_sales_summary():
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    service.odoo_query_service = _FakeOdooQueryService()  # type: ignore[assignment]

    result = service.query(question="Give me a sales summary", user_id=uuid4())

    assert result.intent == AIQueryIntent.summary_query
    assert result.handler == "get_sales_summary"
    assert result.data["revenue_total"] == 10000.0


def test_orchestrator_returns_visualization_for_product_graph_this_year():
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    fake_odoo = _FakeOdooQueryService()
    service.odoo_query_service = fake_odoo  # type: ignore[assignment]
    year = datetime.now(timezone.utc).year

    result = service.query(question="Give me the graph of selling products this year", user_id=uuid4())

    assert result.handler == "get_top_products"
    assert result.visualization is not None
    assert result.visualization.type == "bar"
    assert result.visualization.x_key == "product_name"
    assert result.visualization.y_keys == ["revenue"]
    assert fake_odoo.last_date_from == f"{year}-01-01 00:00:00"
    assert fake_odoo.last_date_to == f"{year}-12-31 23:59:59"
    assert str(year) in result.summary


def test_orchestrator_uses_deterministic_handler_when_text2sql_provider_fails(monkeypatch):
    from app.core.config import settings
    from app.services import ai_orchestrator_service as orchestrator_module

    monkeypatch.setattr(settings, "TEXT2SQL_ENABLED", True)
    monkeypatch.setattr(orchestrator_module, "any_provider_configured", lambda: True)

    class _FailingTextToSQLService:
        def query(self, *, question: str, user_id):
            raise OdooQueryExecutionError("Text-to-SQL provider generation failed.")

    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    fake_odoo = _FakeOdooQueryService()
    service.odoo_query_service = fake_odoo  # type: ignore[assignment]
    service.text_to_sql_service = _FailingTextToSQLService()  # type: ignore[assignment]
    year = datetime.now(timezone.utc).year

    result = service.query(question="top selling products this year", user_id=uuid4())

    assert result.intent == AIQueryIntent.analytics_query
    assert result.handler == "get_top_products"
    assert result.data[0]["product_name"] == "Desk"
    assert fake_odoo.last_date_from == f"{year}-01-01 00:00:00"


def test_orchestrator_returns_prediction_for_best_product_next_year(artifact_tmp_path):
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    _attach_temp_prediction_model(service, artifact_tmp_path)
    target_year = datetime.now(timezone.utc).year + 1

    result = service.query(question="Predict the best product for next year", user_id=uuid4())

    assert result.intent == AIQueryIntent.prediction_query
    assert result.handler == "predict_best_product"
    assert result.prediction is not None
    assert result.prediction.target_period == str(target_year)
    assert result.prediction.best_product.product_name == "Desk"
    assert result.prediction.model_version == "product_prediction_model_v1"
    assert result.prediction.training_rows is not None
    assert result.visualization is not None
    assert result.visualization.x_key == "product_name"
    assert result.visualization.y_keys == ["predicted_revenue"]
    assert "Desk" in result.summary


def test_orchestrator_marks_one_month_prediction_as_limited_estimate(artifact_tmp_path):
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    model_path = artifact_tmp_path / "product_prediction_model.pkl"
    train_product_prediction_model(
        history=[
            {
                "product_id": 2,
                "product_name": "Desk",
                "month": "2026-04",
                "revenue": 900.0,
                "quantity_sold": 4.0,
            },
            {
                "product_id": 3,
                "product_name": "Chair",
                "month": "2026-04",
                "revenue": 500.0,
                "quantity_sold": 5.0,
            },
        ],
        model_path=model_path,
    )
    service.product_prediction_service = ProductPredictionService(model_path=model_path)

    result = service.query(question="Predict the best product for next year", user_id=uuid4())

    assert result.prediction is not None
    assert result.prediction.data_quality == "limited_data"
    assert result.prediction.warning is not None
    assert "Limited-data estimate" in result.summary


def test_orchestrator_accepts_prediction_typo(artifact_tmp_path):
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    _attach_temp_prediction_model(service, artifact_tmp_path)

    result = service.query(question="Predic the best product for next year", user_id=uuid4())

    assert result.intent == AIQueryIntent.prediction_query
    assert result.handler == "predict_best_product"
    assert result.prediction is not None


def test_orchestrator_returns_prediction_unavailable_when_model_corrupt(artifact_tmp_path):
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    model_path = artifact_tmp_path / "product_prediction_model.pkl"
    model_path.write_text("not a joblib model", encoding="utf-8")
    service.product_prediction_service = ProductPredictionService(model_path=model_path)

    result = service.query(question="Predict the best product for next year", user_id=uuid4())

    assert result.intent == AIQueryIntent.prediction_query
    assert result.handler == "predict_best_product"
    assert result.prediction is None
    assert result.data["status"] == "prediction_unavailable"
    assert "could not be loaded" in result.summary


def test_orchestrator_returns_prediction_unavailable_when_model_missing(artifact_tmp_path):
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    service.product_prediction_service = ProductPredictionService(model_path=artifact_tmp_path / "missing.pkl")

    result = service.query(question="Predict the best product for next year", user_id=uuid4())

    assert result.intent == AIQueryIntent.prediction_query
    assert result.handler == "predict_best_product"
    assert result.prediction is None
    assert result.data["status"] == "prediction_unavailable"
    assert "Prediction model file is missing" in result.summary


def test_orchestrator_rejects_unsupported_question():
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    service.odoo_query_service = _FakeOdooQueryService()  # type: ignore[assignment]

    with pytest.raises(AIUnsupportedQuestionError):
        service.query(question="How many employees are on leave this quarter?", user_id=uuid4())


def test_orchestrator_rejects_not_implemented_intent():
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    service.odoo_query_service = _FakeOdooQueryService()  # type: ignore[assignment]

    with pytest.raises(AIIntentNotImplementedError):
        service.query(question="Show me dashboard KPIs", user_id=uuid4())


def test_orchestrator_surfaces_odoo_query_error():
    class _FailingQueryService(_FakeOdooQueryService):
        def get_sales_summary(self, runtime: _FakeRuntime):
            raise OdooQueryExecutionError("Odoo read_group failed")

    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    service.odoo_query_service = _FailingQueryService()  # type: ignore[assignment]

    with pytest.raises(OdooQueryExecutionError):
        service.query(question="sales summary", user_id=uuid4())


def test_orchestrator_routes_general_query_when_handler_not_found(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "TEXT2SQL_ENABLED", True)

    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    service.odoo_query_service = _FakeOdooQueryService()  # type: ignore[assignment]
    
    class _FakeTextToSQLService:
        def __init__(self) -> None:
            class _FakeProviderChain:
                def generate_general(self, *, system_prompt: str, user_prompt: str, detected_language=None):
                    return {
                        "summary": "Hello there! I am MyERPPilot AI.",
                        "suggested_actions": ["Show top products"]
                    }
            self.provider_chain = _FakeProviderChain()
            
        def query(self, *, question: str, user_id: uuid4, detected_language=None):
            from app.services.text_to_sql_service import TextToSQLCannotAnswerError
            raise TextToSQLCannotAnswerError("Cannot answer this question")
            
    service.text_to_sql_service = _FakeTextToSQLService()  # type: ignore[assignment]
    
    result = service.query(question="HEY", user_id=uuid4())
    
    assert result.intent == AIQueryIntent.general_query
    assert result.handler == "general_knowledge"
    assert result.summary == "Hello there! I am MyERPPilot AI."
    assert result.suggested_actions == ["Show top products"]
    assert result.summary == "Hello there! I am MyERPPilot AI."
    assert result.suggested_actions == ["Show top products"]


def test_orchestrator_fallback_to_general_query_on_cannot_answer_error(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "TEXT2SQL_ENABLED", True)
    
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    service.odoo_query_service = _FakeOdooQueryService()  # type: ignore[assignment]
    
    class _FakeTextToSQLService:
        def __init__(self) -> None:
            class _FakeProviderChain:
                def generate_general(self, *, system_prompt: str, user_prompt: str, detected_language=None):
                    return {
                        "summary": "This is a general reply.",
                        "suggested_actions": ["Show top products"]
                    }
            self.provider_chain = _FakeProviderChain()
            
        def query(self, *, question: str, user_id: uuid4, detected_language=None):
            from app.services.text_to_sql_service import TextToSQLCannotAnswerError
            raise TextToSQLCannotAnswerError("Cannot answer this question")
            
    service.text_to_sql_service = _FakeTextToSQLService()  # type: ignore[assignment]
    
    result = service.query(question="explain what ERP is", user_id=uuid4())
    
    assert result.intent == AIQueryIntent.general_query
    assert result.handler == "general_knowledge"
    assert result.summary == "This is a general reply."



def test_orchestrator_returns_arabic_general_fallback(monkeypatch):
    from app.core.config import settings
    from app.services import ai_orchestrator_service as orchestrator_module

    monkeypatch.setattr(settings, "TEXT2SQL_ENABLED", True)
    monkeypatch.setattr(orchestrator_module, "any_provider_configured", lambda: True)

    class _FailingTextToSQLService:
        class _FailingProviderChain:
            def generate_general(self, *, system_prompt: str, user_prompt: str, detected_language=None):
                raise RuntimeError("provider unavailable")

        provider_chain = _FailingProviderChain()

        def query(self, *, question: str, user_id, detected_language=None):
            from app.services.text_to_sql_service import TextToSQLCannotAnswerError
            raise TextToSQLCannotAnswerError("Cannot answer this question")

    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    service.odoo_query_service = _FakeOdooQueryService()  # type: ignore[assignment]
    service.text_to_sql_service = _FailingTextToSQLService()  # type: ignore[assignment]

    result = service.query(question="مرحبا", user_id=uuid4())

    assert result.intent == AIQueryIntent.general_query
    assert result.handler == "general_knowledge"
    assert result.meta.detected_language == "ar"
    assert "مرحبا" in result.summary
    assert result.suggested_actions[0].startswith("اعرض")
