"""
Helpers for building consistent AI query responses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.schemas.ai import AIQueryIntent, AIQueryMeta, AIQueryResponse, AIVisualizationSpec


def build_ai_response(
    *,
    question: str,
    intent: AIQueryIntent,
    handler: str,
    summary: str,
    data: list[dict[str, Any]] | dict[str, Any] | None,
    suggested_actions: list[str] | None = None,
    source: str = "odoo",
    meta_overrides: dict[str, Any] | None = None,
    visualization: AIVisualizationSpec | None = None,
) -> AIQueryResponse:
    normalized_data = _normalize_data_payload(data)
    meta_payload = {
        "count": _compute_result_count(normalized_data),
        "generated_at": datetime.now(timezone.utc),
    }
    if meta_overrides:
        meta_payload.update(meta_overrides)

    return AIQueryResponse(
        question=question,
        intent=intent,
        source=source,
        handler=handler,
        summary=summary,
        data=normalized_data,
        suggested_actions=suggested_actions or [],
        visualization=visualization,
        meta=AIQueryMeta(**meta_payload),
    )


def _normalize_data_payload(
    data: list[dict[str, Any]] | dict[str, Any] | None,
) -> list[dict[str, Any]] | dict[str, Any]:
    if data is None:
        return []

    # Defensive flattening to avoid leaking legacy nested "data: {data: ...}" payloads.
    if (
        isinstance(data, dict)
        and set(data.keys()) == {"data"}
        and isinstance(data["data"], (list, dict))
    ):
        data = data["data"]

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return data
    return []


def _compute_result_count(data: list[dict[str, Any]] | dict[str, Any]) -> int:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return 1 if data else 0
    return 0
