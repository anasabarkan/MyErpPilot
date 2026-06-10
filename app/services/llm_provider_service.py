"""
LLM provider abstraction for Text-to-SQL structured generation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import settings


class LLMProviderError(RuntimeError):
    """Raised when an LLM provider call fails or returns invalid output."""


@dataclass(frozen=True)
class TextToSQLDraft:
    can_answer: bool
    sql: str
    summary: str
    assumptions: list[str]
    provider: str
    model: str


class LLMProvider(Protocol):
    provider_name: str
    model: str

    def generate(self, *, system_prompt: str, user_prompt: str) -> TextToSQLDraft:
        ...


TEXT_TO_SQL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["can_answer", "sql", "summary", "assumptions"],
    "properties": {
        "can_answer": {"type": "boolean"},
        "sql": {"type": "string"},
        "summary": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
}


class OpenAITextToSQLProvider:
    provider_name = "openai"

    def __init__(self) -> None:
        self.model = settings.OPENAI_TEXT2SQL_MODEL

    def generate(self, *, system_prompt: str, user_prompt: str) -> TextToSQLDraft:
        if not settings.OPENAI_API_KEY:
            raise LLMProviderError("OpenAI API key is not configured.")
        try:
            from openai import OpenAI

            client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.TEXT2SQL_TIMEOUT_SECONDS)
            response = client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "text_to_sql",
                        "schema": TEXT_TO_SQL_JSON_SCHEMA,
                        "strict": True,
                    }
                },
            )
            content = getattr(response, "output_text", None)
            if not content:
                raise LLMProviderError("OpenAI returned no structured content.")
            return self._parse(content)
        except LLMProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider boundary
            raise LLMProviderError("OpenAI Text-to-SQL generation failed.") from exc

    def _parse(self, content: str) -> TextToSQLDraft:
        payload = _parse_payload(content)
        return _draft_from_payload(payload, provider=self.provider_name, model=self.model)


class GeminiTextToSQLProvider:
    provider_name = "gemini"

    def __init__(self) -> None:
        self.model = settings.GEMINI_TEXT2SQL_MODEL

    def generate(self, *, system_prompt: str, user_prompt: str) -> TextToSQLDraft:
        if not settings.GEMINI_API_KEY:
            raise LLMProviderError("Gemini API key is not configured.")
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            response = client.models.generate_content(
                model=self.model,
                contents=f"{system_prompt}\n\n{user_prompt}",
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TEXT_TO_SQL_JSON_SCHEMA,
                    temperature=0,
                ),
            )
            if not response.text:
                raise LLMProviderError("Gemini returned no structured content.")
            return self._parse(response.text)
        except LLMProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider boundary
            raise LLMProviderError("Gemini Text-to-SQL generation failed.") from exc

    def _parse(self, content: str) -> TextToSQLDraft:
        payload = _parse_payload(content)
        return _draft_from_payload(payload, provider=self.provider_name, model=self.model)


class TextToSQLProviderChain:
    def __init__(self, providers: list[LLMProvider] | None = None) -> None:
        self.providers = providers or [OpenAITextToSQLProvider(), GeminiTextToSQLProvider()]

    def generate(self, *, system_prompt: str, user_prompt: str) -> tuple[TextToSQLDraft, bool]:
        failures: list[str] = []
        for index, provider in enumerate(self.providers):
            try:
                return provider.generate(system_prompt=system_prompt, user_prompt=user_prompt), index > 0
            except LLMProviderError as exc:
                failures.append(f"{provider.provider_name}: {exc}")
        raise LLMProviderError("All Text-to-SQL providers failed.")


def any_provider_configured() -> bool:
    return bool(settings.OPENAI_API_KEY or settings.GEMINI_API_KEY)


def _parse_payload(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMProviderError("Provider returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise LLMProviderError("Provider JSON root must be an object.")
    return payload


def _draft_from_payload(payload: dict[str, Any], *, provider: str, model: str) -> TextToSQLDraft:
    try:
        can_answer = bool(payload["can_answer"])
        sql = str(payload["sql"]).strip()
        summary = str(payload["summary"]).strip()
        assumptions_payload = payload["assumptions"]
    except KeyError as exc:
        raise LLMProviderError("Provider response is missing required fields.") from exc

    assumptions = [str(item) for item in assumptions_payload] if isinstance(assumptions_payload, list) else []
    return TextToSQLDraft(
        can_answer=can_answer,
        sql=sql,
        summary=summary,
        assumptions=assumptions,
        provider=provider,
        model=model,
    )
