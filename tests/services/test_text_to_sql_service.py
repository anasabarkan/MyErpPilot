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
        self.last_system_prompt = None
        self.last_user_prompt = None

    def generate(self, *, system_prompt: str, user_prompt: str):
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
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
    provider_chain = _FakeProviderChain(
            TextToSQLDraft(
                can_answer=True,
                sql="SELECT id, name FROM sale_order LIMIT 5",
                summary="Recent orders.",
                assumptions=[],
                provider="openai",
                model="gpt-5.4-mini",
            )
        )
    service = _service(provider_chain)

    result = service.query(question="recent orders", user_id=uuid4())

    assert result.handler == "text_to_sql"
    assert result.data == [{"id": 1, "name": "S00001"}]
    assert result.meta.provider == "openai"
    assert result.meta.generated_sql == "SELECT id, name FROM sale_order LIMIT 5"
    assert result.meta.row_count == 1
    assert result.meta.detected_language == "en"
    assert provider_chain.last_user_prompt is not None
    assert "Response language: English." in provider_chain.last_user_prompt


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


def test_text_to_sql_prompt_uses_detected_french_language():
    provider_chain = _FakeProviderChain(
        TextToSQLDraft(
            can_answer=True,
            sql="SELECT id, name FROM sale_order LIMIT 5",
            summary="Commandes recentes.",
            assumptions=[],
            provider="openai",
            model="gpt-5.4-mini",
        )
    )
    service = _service(provider_chain)

    result = service.query(question="Quelles sont les commandes recentes ?", user_id=uuid4())

    assert result.meta.detected_language == "fr"
    assert provider_chain.last_user_prompt is not None
    assert "Response language: French." in provider_chain.last_user_prompt
