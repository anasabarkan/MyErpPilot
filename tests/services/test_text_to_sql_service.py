from dataclasses import dataclass
from uuid import uuid4

import pytest

from app.services.llm_provider_service import LLMProviderError, TextToSQLDraft
from app.services.sql_validation_service import SQLValidationService, ValidatedSQL
from app.services.text_to_sql_service import TextToSQLCannotAnswerError, TextToSQLService


@dataclass
class _FakeSource:
    database_name: str = "odoo"


@dataclass
class _FakeRuntime:
    data_source: _FakeSource


class _FakeOdooQueryService:
    def build_runtime_context(self):
        return _FakeRuntime(data_source=_FakeSource())


class _FakeProviderChain:
    def __init__(self, draft, fallback_used=False):
        self.draft = draft
        self.fallback_used = fallback_used

    def generate(self, *, system_prompt: str, user_prompt: str):
        return self.draft, self.fallback_used


class _FailingThenGeminiProviderChain:
    def generate(self, *, system_prompt: str, user_prompt: str):
        return (
            TextToSQLDraft(
                can_answer=True,
                sql="SELECT id, name FROM sale_order LIMIT 5",
                summary="Recent orders.",
                assumptions=[],
                provider="gemini",
                model="gemini-2.5-flash",
            ),
            True,
        )


class _FakeExecutor:
    def __init__(self):
        self.last_sql = None

    def execute(self, validated_sql: ValidatedSQL):
        self.last_sql = validated_sql.sql
        return [{"id": 1, "name": "S00001"}], 12


def _service(provider_chain):
    service = TextToSQLService(
        db=object(),  # type: ignore[arg-type]
        provider_chain=provider_chain,
        validator=SQLValidationService(max_rows=100),
        executor=_FakeExecutor(),  # type: ignore[arg-type]
    )
    service.odoo_query_service = _FakeOdooQueryService()  # type: ignore[assignment]
    return service


def test_text_to_sql_returns_compatible_response():
    service = _service(
        _FakeProviderChain(
            TextToSQLDraft(
                can_answer=True,
                sql="SELECT id, name FROM sale_order LIMIT 5",
                summary="Recent orders.",
                assumptions=[],
                provider="openai",
                model="gpt-5.4-mini",
            )
        )
    )

    result = service.query(question="recent orders", user_id=uuid4())

    assert result.handler == "text_to_sql"
    assert result.data == [{"id": 1, "name": "S00001"}]
    assert result.meta.provider == "openai"
    assert result.meta.generated_sql == "SELECT id, name FROM sale_order LIMIT 5"
    assert result.meta.row_count == 1


def test_text_to_sql_records_gemini_fallback():
    service = _service(_FailingThenGeminiProviderChain())

    result = service.query(question="recent orders", user_id=uuid4())

    assert result.meta.provider == "gemini"
    assert result.meta.fallback_used is True


def test_text_to_sql_invalid_sql_does_not_call_second_provider():
    service = _service(
        _FakeProviderChain(
            TextToSQLDraft(
                can_answer=True,
                sql="DELETE FROM sale_order",
                summary="Unsafe.",
                assumptions=[],
                provider="openai",
                model="gpt-5.4-mini",
            )
        )
    )

    with pytest.raises(TextToSQLCannotAnswerError):
        service.query(question="delete orders", user_id=uuid4())


def test_text_to_sql_provider_failure_is_controlled():
    class _FailingProviderChain:
        def generate(self, *, system_prompt: str, user_prompt: str):
            raise LLMProviderError("all failed")

    service = _service(_FailingProviderChain())

    with pytest.raises(Exception, match="provider generation failed"):
        service.query(question="recent orders", user_id=uuid4())
