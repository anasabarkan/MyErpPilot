"""
Schemas for AI query orchestration endpoints.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AIQueryIntent(str, Enum):
    analytics_query = "analytics_query"
    dashboard_query = "dashboard_query"
    summary_query = "summary_query"
    prediction_query = "prediction_query"
    action_proposal = "action_proposal"
    general_query = "general_query"


class AIVisualizationType(str, Enum):
    bar = "bar"
    line = "line"
    area = "area"
    pie = "pie"


class AIVisualizationSpec(BaseModel):
    type: AIVisualizationType
    title: str
    data: list[dict[str, Any]] = Field(default_factory=list)
    x_key: str
    y_keys: list[str] = Field(default_factory=list)
    x_label: str | None = None
    y_label: str | None = None
    description: str | None = None


class AIPredictionCandidate(BaseModel):
    product_id: int | None = None
    product_name: str
    predicted_revenue: float
    predicted_quantity: float
    historical_revenue: float
    recent_revenue: float
    previous_revenue: float
    growth_rate: float
    consistency_score: float
    confidence: float
    months_observed: int


class AIPredictionResult(BaseModel):
    target_period: str
    best_product: AIPredictionCandidate
    candidates: list[AIPredictionCandidate] = Field(default_factory=list)
    analysis: list[str] = Field(default_factory=list)
    methodology: str
    confidence: float
    model_version: str | None = None
    trained_at: str | None = None
    training_rows: int | None = None
    data_quality: str | None = None
    warning: str | None = None
    months_count: int | None = None
    first_month: str | None = None
    last_month: str | None = None
    fallback_training_rows: int | None = None


class AIQueryRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000)
    session_id: UUID | None = None


class AIQueryMeta(BaseModel):
    count: int = 0
    generated_at: datetime
    detected_language: str | None = None
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
    visualization: AIVisualizationSpec | None = None
    prediction: AIPredictionResult | None = None
    meta: AIQueryMeta
