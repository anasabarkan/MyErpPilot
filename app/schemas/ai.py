"""
Schemas for AI query orchestration endpoints.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AIQueryIntent(str, Enum):
    analytics_query = "analytics_query"
    dashboard_query = "dashboard_query"
    summary_query = "summary_query"
    action_proposal = "action_proposal"


class AIQueryRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000)


class AIQueryMeta(BaseModel):
    count: int = 0
    generated_at: datetime
    provider: str | None = None
    model: str | None = None
    fallback_used: bool | None = None
    generated_sql: str | None = None
    execution_ms: int | None = None
    row_count: int | None = None


class AIQueryResponse(BaseModel):
    question: str
    intent: AIQueryIntent
    source: str = "odoo"
    handler: str
    data: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)
    summary: str
    suggested_actions: list[str] = Field(default_factory=list)
    meta: AIQueryMeta
