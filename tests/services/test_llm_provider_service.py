import pytest

from app.services.llm_provider_service import LLMProviderError, TextToSQLDraft, TextToSQLProviderChain


class _SuccessfulProvider:
    provider_name = "openai"
    model = "gpt-5.4-mini"

    def generate(self, *, system_prompt: str, user_prompt: str):
        return TextToSQLDraft(
            can_answer=True,
            sql="SELECT id FROM sale_order LIMIT 5",
            summary="Orders.",
            assumptions=[],
            provider=self.provider_name,
            model=self.model,
        )


class _FailingProvider:
    provider_name = "openai"
    model = "gpt-5.4-mini"

    def generate(self, *, system_prompt: str, user_prompt: str):
        raise LLMProviderError("provider failed")


class _GeminiProvider(_SuccessfulProvider):
    provider_name = "gemini"
    model = "gemini-2.5-flash"


def test_provider_chain_returns_openai_without_fallback():
    draft, fallback_used = TextToSQLProviderChain(providers=[_SuccessfulProvider()]).generate(
        system_prompt="system",
        user_prompt="user",
    )

    assert draft.provider == "openai"
    assert fallback_used is False


def test_provider_chain_uses_gemini_after_openai_failure():
    draft, fallback_used = TextToSQLProviderChain(
        providers=[_FailingProvider(), _GeminiProvider()]
    ).generate(system_prompt="system", user_prompt="user")

    assert draft.provider == "gemini"
    assert fallback_used is True


def test_provider_chain_raises_when_all_fail():
    with pytest.raises(LLMProviderError):
        TextToSQLProviderChain(providers=[_FailingProvider()]).generate(
            system_prompt="system",
            user_prompt="user",
        )
