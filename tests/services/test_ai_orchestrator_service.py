from dataclasses import dataclass
from uuid import uuid4

import pytest

from app.schemas.ai import AIQueryIntent
from app.services.ai_orchestrator_service import (
    AIIntentNotImplementedError,
    AIOrchestratorService,
    AIUnsupportedQuestionError,
)
from app.services.odoo_query_service import OdooQueryExecutionError


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

    def build_runtime_context(self) -> _FakeRuntime:
        return _FakeRuntime()

    def get_top_customers(self, runtime: _FakeRuntime, *, limit: int = 5):
        self.last_limit = limit
        return [{"customer_id": 1, "customer_name": "Agrolait", "sales_amount": 1500.0}]

    def get_top_products(self, runtime: _FakeRuntime, *, limit: int = 5):
        return [{"product_id": 2, "product_name": "Desk", "sales_amount": 900.0}]

    def get_sales_summary(self, runtime: _FakeRuntime):
        return {"revenue_total": 10000.0}

    def get_invoice_status_summary(self, runtime: _FakeRuntime):
        return {"paid_invoices": 8, "unpaid_invoices": 2}

    def get_recent_orders(self, runtime: _FakeRuntime, *, limit: int = 10):
        self.last_limit = limit
        return [{"id": 10, "name": "SO010"}]


def test_orchestrator_routes_top_customers_and_parses_limit():
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    fake_odoo = _FakeOdooQueryService()
    service.odoo_query_service = fake_odoo  # type: ignore[assignment]

    result = service.query(question="Who are my top 3 customers?", user_id=uuid4())

    assert result.intent == AIQueryIntent.analytics_query
    assert result.handler == "get_top_customers"
    assert fake_odoo.last_limit == 3
    assert result.data[0]["customer_name"] == "Agrolait"


def test_orchestrator_returns_summary_intent_for_sales_summary():
    service = AIOrchestratorService(db=object())  # type: ignore[arg-type]
    service.odoo_query_service = _FakeOdooQueryService()  # type: ignore[assignment]

    result = service.query(question="Give me a sales summary", user_id=uuid4())

    assert result.intent == AIQueryIntent.summary_query
    assert result.handler == "get_sales_summary"
    assert result.data["revenue_total"] == 10000.0


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
                def generate_general(self, *, system_prompt: str, user_prompt: str):
                    return {
                        "summary": "Hello there! I am MyERPPilot AI.",
                        "suggested_actions": ["Show top products"]
                    }
            self.provider_chain = _FakeProviderChain()
            
        def query(self, *, question: str, user_id: uuid4):
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
                def generate_general(self, *, system_prompt: str, user_prompt: str):
                    return {
                        "summary": "This is a general reply.",
                        "suggested_actions": ["Show top products"]
                    }
            self.provider_chain = _FakeProviderChain()
            
        def query(self, *, question: str, user_id: uuid4):
            from app.services.text_to_sql_service import TextToSQLCannotAnswerError
            raise TextToSQLCannotAnswerError("Cannot answer this question")
            
    service.text_to_sql_service = _FakeTextToSQLService()  # type: ignore[assignment]
    
    result = service.query(question="explain what ERP is", user_id=uuid4())
    
    assert result.intent == AIQueryIntent.general_query
    assert result.handler == "general_knowledge"
    assert result.summary == "This is a general reply."

