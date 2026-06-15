"""
PKL-backed product prediction service.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.schemas.ai import AIPredictionCandidate, AIPredictionResult


MODEL_VERSION = "product_prediction_model_v1"
DATA_QUALITY_LIMITED = "limited_data"
DATA_QUALITY_SHORT = "short_history"
DATA_QUALITY_HISTORICAL = "historical"
TARGET_NAMES = ["next_revenue", "next_quantity"]
FEATURE_NAMES = [
    "product_key",
    "product_name",
    "month_index",
    "calendar_month",
    "current_revenue",
    "current_quantity",
    "lag_revenue_1",
    "lag_revenue_2",
    "lag_quantity_1",
    "lag_quantity_2",
    "rolling_revenue_3",
    "rolling_quantity_3",
    "recent_revenue_6",
    "recent_quantity_6",
    "growth_rate",
    "consistency_score",
    "months_observed",
]
CATEGORICAL_FEATURE_INDICES = [0, 1]
NUMERIC_FEATURE_INDICES = list(range(2, len(FEATURE_NAMES)))


class ProductPredictionInsufficientDataError(RuntimeError):
    """Raised when Odoo sales history is not sufficient for product prediction."""


class ProductPredictionDependencyError(RuntimeError):
    """Raised when prediction dependencies are unavailable."""


class ProductPredictionModelMissingError(RuntimeError):
    """Raised when no trained prediction artifact exists."""


class ProductPredictionModelLoadError(RuntimeError):
    """Raised when a trained prediction artifact cannot be loaded."""


@dataclass(frozen=True)
class PredictionDiagnostics:
    status: str
    model_path: str
    model_version: str | None = None
    trained_at: str | None = None
    data_quality: str | None = None
    warning: str | None = None
    months_count: int | None = None
    products_count: int | None = None
    training_rows: int | None = None
    fallback_training_rows: int | None = None


@dataclass(frozen=True)
class ProductMonthlySeries:
    product_key: str
    product_id: int | None
    product_name: str
    months: list[str]
    revenue: list[float]
    quantity: list[float]


@dataclass(frozen=True)
class TrainingDataset:
    features: list[list[object]]
    targets: list[list[float]]
    product_states: list[dict[str, Any]]
    months: list[str]
    fallback_training_rows: int = 0


@dataclass
class _CachedArtifact:
    path: Path
    modified_at: float
    payload: dict[str, Any]


class ProductPredictionService:
    def __init__(self, *, model_path: str | Path | None = None) -> None:
        self.model_path = Path(model_path or settings.PREDICTION_MODEL_PATH)
        self._cached_artifact: _CachedArtifact | None = None

    def predict_best_product(
        self,
        *,
        target_year: int,
        limit: int = 5,
    ) -> AIPredictionResult:
        artifact = self._load_artifact()
        candidates = self._predict_candidates(
            artifact=artifact,
            target_year=target_year,
            limit=limit,
        )
        if not candidates:
            raise ProductPredictionInsufficientDataError(
                "The trained prediction model does not contain enough product state to forecast."
            )

        best_product = candidates[0]
        metadata = artifact.get("metadata", {})
        data_quality = _metadata_string(metadata, "data_quality") or DATA_QUALITY_LIMITED
        warning = _prediction_warning(data_quality, metadata)
        return AIPredictionResult(
            target_period=str(target_year),
            best_product=best_product,
            candidates=candidates,
            analysis=self._build_analysis(best_product, data_quality=data_quality, metadata=metadata),
            methodology=_prediction_methodology(data_quality),
            confidence=best_product.confidence,
            model_version=str(metadata.get("model_version") or MODEL_VERSION),
            trained_at=_metadata_string(metadata, "trained_at"),
            training_rows=_metadata_int(metadata, "training_rows"),
            data_quality=data_quality,
            warning=warning,
            months_count=_metadata_int(metadata, "months_count"),
            first_month=_metadata_string(metadata, "first_month"),
            last_month=_metadata_string(metadata, "last_month"),
            fallback_training_rows=_metadata_int(metadata, "fallback_training_rows"),
        )

    def diagnose_model(self) -> PredictionDiagnostics:
        model_path = self._resolved_model_path()
        if not model_path.exists():
            return PredictionDiagnostics(
                status="missing",
                model_path=str(model_path),
                warning="Prediction model file is missing. Run python -m app.scripts.train_product_prediction_model.",
            )

        try:
            artifact = self._load_artifact()
        except ProductPredictionDependencyError:
            raise
        except ProductPredictionModelLoadError as exc:
            return PredictionDiagnostics(
                status="unusable",
                model_path=str(model_path),
                warning=str(exc),
            )

        metadata = artifact.get("metadata", {}) if isinstance(artifact.get("metadata"), dict) else {}
        data_quality = _metadata_string(metadata, "data_quality") or DATA_QUALITY_LIMITED
        status = "healthy" if data_quality == DATA_QUALITY_HISTORICAL else data_quality
        return PredictionDiagnostics(
            status=status,
            model_path=str(model_path),
            model_version=_metadata_string(metadata, "model_version") or MODEL_VERSION,
            trained_at=_metadata_string(metadata, "trained_at"),
            data_quality=data_quality,
            warning=_prediction_warning(data_quality, metadata),
            months_count=_metadata_int(metadata, "months_count"),
            products_count=_metadata_int(metadata, "products_count"),
            training_rows=_metadata_int(metadata, "training_rows"),
            fallback_training_rows=_metadata_int(metadata, "fallback_training_rows"),
        )

    def _predict_candidates(
        self,
        *,
        artifact: dict[str, Any],
        target_year: int,
        limit: int,
    ) -> list[AIPredictionCandidate]:
        pipeline = artifact.get("pipeline")
        product_states = artifact.get("product_states")
        if pipeline is None or not isinstance(product_states, list):
            raise ProductPredictionModelLoadError("Prediction artifact is missing required model payload.")

        metadata = artifact.get("metadata", {}) if isinstance(artifact.get("metadata"), dict) else {}
        data_quality = _metadata_string(metadata, "data_quality") or DATA_QUALITY_LIMITED
        candidates: list[AIPredictionCandidate] = []
        for raw_state in product_states:
            series = _series_from_state(raw_state)
            if series is None:
                continue
            if data_quality == DATA_QUALITY_LIMITED:
                predicted_revenue, predicted_quantity = _limited_data_year_estimate(series)
            else:
                predicted_revenue, predicted_quantity = self._forecast_product_year(
                    pipeline=pipeline,
                    series=series,
                    target_year=target_year,
                )
            if predicted_revenue <= 0:
                continue

            recent_revenue = sum(series.revenue[-6:])
            previous_revenue = sum(series.revenue[-12:-6]) if len(series.revenue) >= 12 else sum(series.revenue[:-6])
            historical_revenue = sum(series.revenue)
            months_observed = sum(1 for value in series.revenue if value > 0)
            growth_rate = _growth_rate(recent_revenue, previous_revenue)
            consistency_score = _consistency_score([value for value in series.revenue if value > 0])
            confidence = _confidence_score(
                months_total=len(series.months),
                months_observed=months_observed,
                consistency_score=consistency_score,
                growth_rate=growth_rate,
                data_quality=data_quality,
            )

            candidates.append(
                AIPredictionCandidate(
                    product_id=series.product_id,
                    product_name=series.product_name,
                    predicted_revenue=round(predicted_revenue, 2),
                    predicted_quantity=round(predicted_quantity, 2),
                    historical_revenue=round(historical_revenue, 2),
                    recent_revenue=round(recent_revenue, 2),
                    previous_revenue=round(previous_revenue, 2),
                    growth_rate=round(growth_rate, 4),
                    consistency_score=round(consistency_score, 4),
                    confidence=round(confidence, 4),
                    months_observed=months_observed,
                )
            )

        return sorted(
            candidates,
            key=lambda candidate: (
                -candidate.predicted_revenue,
                -candidate.confidence,
                candidate.product_name.lower(),
            ),
        )[: max(1, limit)]

    def _forecast_product_year(
        self,
        *,
        pipeline: Any,
        series: ProductMonthlySeries,
        target_year: int,
    ) -> tuple[float, float]:
        months = list(series.months)
        revenue = list(series.revenue)
        quantity = list(series.quantity)
        current_month = months[-1]
        target_end = f"{target_year}-12"

        safety_counter = 0
        while current_month < target_end and safety_counter < 84:
            next_month = _add_months(current_month, 1)
            feature_row = build_feature_row(
                product_key=series.product_key,
                product_name=series.product_name,
                month=next_month,
                month_index=len(months),
                revenue=revenue,
                quantity=quantity,
            )
            predicted = pipeline.predict([feature_row])[0]
            next_revenue = max(_number_value(predicted[0]), 0.0)
            next_quantity = max(_number_value(predicted[1]), 0.0)

            months.append(next_month)
            revenue.append(next_revenue)
            quantity.append(next_quantity)
            current_month = next_month
            safety_counter += 1

            if next_month.startswith(f"{target_year}-"):
                continue

        predicted_revenue = 0.0
        predicted_quantity = 0.0
        for month, month_revenue, month_quantity in zip(months, revenue, quantity, strict=False):
            if month.startswith(f"{target_year}-"):
                predicted_revenue += month_revenue
                predicted_quantity += month_quantity
        return predicted_revenue, predicted_quantity

    def _load_artifact(self) -> dict[str, Any]:
        model_path = self._resolved_model_path()
        if not model_path.exists():
            raise ProductPredictionModelMissingError(
                f"Prediction model file is missing at {model_path}. Run: python -m app.scripts.train_product_prediction_model"
            )

        modified_at = model_path.stat().st_mtime
        if (
            self._cached_artifact
            and self._cached_artifact.path == model_path
            and self._cached_artifact.modified_at == modified_at
        ):
            return self._cached_artifact.payload

        joblib = _import_joblib()
        try:
            payload = joblib.load(model_path)
        except Exception as exc:  # noqa: BLE001 - return clean chat-level error
            raise ProductPredictionModelLoadError(
                f"Prediction model file could not be loaded: {exc}"
            ) from exc

        if not isinstance(payload, dict) or payload.get("model_version") != MODEL_VERSION:
            raise ProductPredictionModelLoadError("Prediction model file has an unsupported format.")

        self._cached_artifact = _CachedArtifact(path=model_path, modified_at=modified_at, payload=payload)
        return payload

    def _resolved_model_path(self) -> Path:
        if self.model_path.is_absolute():
            return self.model_path
        return Path.cwd() / self.model_path

    @staticmethod
    def _build_analysis(
        candidate: AIPredictionCandidate,
        *,
        data_quality: str,
        metadata: dict[str, Any],
    ) -> list[str]:
        growth_percent = candidate.growth_rate * 100
        confidence_percent = candidate.confidence * 100
        reason = [
            f"{candidate.product_name} was selected because it has the highest projected revenue among the candidate products.",
            f"The estimate used {metadata.get('months_count', candidate.months_observed)} month(s) of Odoo sales history from {metadata.get('first_month', 'unknown')} to {metadata.get('last_month', 'unknown')}.",
            f"Recent revenue is {growth_percent:.1f}% compared with the previous comparable period.",
            f"The product has {candidate.months_observed} month(s) with sales activity and {candidate.historical_revenue:,.2f} historical revenue in the training data.",
            f"Sales consistency score is {candidate.consistency_score * 100:.1f}%, supporting a {confidence_percent:.1f}% confidence estimate.",
        ]
        if data_quality == DATA_QUALITY_LIMITED:
            reason.insert(
                0,
                "Limited data warning: this is a conservative run-rate estimate, not a strong machine-learning forecast.",
            )
        elif data_quality == DATA_QUALITY_SHORT:
            reason.insert(0, "Short history warning: this forecast uses fewer than 12 months of sales history.")
        return reason


def train_product_prediction_model(
    *,
    history: list[dict[str, Any]],
    model_path: str | Path,
    metadata_path: str | Path | None = None,
    history_months: int = 36,
) -> dict[str, Any]:
    sklearn = _import_sklearn()
    joblib = _import_joblib()
    dataset = build_training_dataset(history)
    if not dataset.features:
        raise ProductPredictionInsufficientDataError(
            "At least 1 product sales month with positive revenue is required to train the prediction model."
        )

    preprocessor = sklearn["ColumnTransformer"](
        transformers=[
            ("categorical", sklearn["OneHotEncoder"](handle_unknown="ignore"), CATEGORICAL_FEATURE_INDICES),
            ("numeric", "passthrough", NUMERIC_FEATURE_INDICES),
        ]
    )
    pipeline = sklearn["Pipeline"](
        steps=[
            ("features", preprocessor),
            (
                "model",
                sklearn["RandomForestRegressor"](
                    n_estimators=200,
                    random_state=42,
                    min_samples_leaf=1,
                ),
            ),
        ]
    )
    pipeline.fit(dataset.features, dataset.targets)
    model_score = _safe_model_score(pipeline, dataset.features, dataset.targets)
    trained_at = datetime.now(timezone.utc).isoformat()
    metadata = {
        "model_version": MODEL_VERSION,
        "trained_at": trained_at,
        "training_rows": len(dataset.features),
        "fallback_training_rows": dataset.fallback_training_rows,
        "data_quality": _training_data_quality(dataset),
        "history_months": history_months,
        "products_count": len(dataset.product_states),
        "months_count": len(dataset.months),
        "first_month": dataset.months[0],
        "last_month": dataset.months[-1],
        "feature_names": FEATURE_NAMES,
        "target_names": TARGET_NAMES,
        "model_score": round(model_score, 6) if model_score is not None else None,
    }
    payload = {
        "model_version": MODEL_VERSION,
        "pipeline": pipeline,
        "product_states": dataset.product_states,
        "metadata": metadata,
    }

    model_path = Path(model_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, model_path)

    if metadata_path is None:
        metadata_path = model_path.with_suffix(".meta.json")
    metadata_path = Path(metadata_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def build_training_dataset(history: list[dict[str, Any]]) -> TrainingDataset:
    series_items = build_product_monthly_series(history)
    if not series_items:
        raise ProductPredictionInsufficientDataError(
            "Not enough product sales history to build training rows."
        )

    features: list[list[object]] = []
    targets: list[list[float]] = []
    fallback_training_rows = 0
    for series in series_items:
        for index in range(0, len(series.months) - 1):
            features.append(
                build_feature_row(
                    product_key=series.product_key,
                    product_name=series.product_name,
                    month=series.months[index],
                    month_index=index,
                    revenue=series.revenue[: index + 1],
                    quantity=series.quantity[: index + 1],
                )
            )
            targets.append([series.revenue[index + 1], series.quantity[index + 1]])

        if len(series.months) == 1:
            features.append(
                build_feature_row(
                    product_key=series.product_key,
                    product_name=series.product_name,
                    month=series.months[0],
                    month_index=0,
                    revenue=series.revenue,
                    quantity=series.quantity,
                )
            )
            targets.append([series.revenue[0], series.quantity[0]])
            fallback_training_rows += 1

    months = sorted({month for series in series_items for month in series.months})
    return TrainingDataset(
        features=features,
        targets=targets,
        product_states=[_state_from_series(series) for series in series_items],
        months=months,
        fallback_training_rows=fallback_training_rows,
    )


def build_product_monthly_series(history: list[dict[str, Any]]) -> list[ProductMonthlySeries]:
    months = sorted(
        {
            str(row.get("month"))
            for row in history
            if isinstance(row.get("month"), str) and len(str(row.get("month"))) == 7
        }
    )
    if not months:
        raise ProductPredictionInsufficientDataError(
            "No dated product sales history is available for training."
        )

    grouped: dict[str, dict[str, Any]] = {}
    for row in history:
        product_name = _string_value(row.get("product_name"))
        month = _string_value(row.get("month"))
        if not product_name or month not in months:
            continue

        product_id = row.get("product_id") if isinstance(row.get("product_id"), int) else None
        product_key = str(product_id) if product_id is not None else product_name.lower()
        entry = grouped.setdefault(
            product_key,
            {
                "product_key": product_key,
                "product_id": product_id,
                "product_name": product_name,
                "revenue_by_month": {},
                "quantity_by_month": {},
            },
        )
        entry["revenue_by_month"][month] = entry["revenue_by_month"].get(month, 0.0) + _number_value(row.get("revenue"))
        entry["quantity_by_month"][month] = entry["quantity_by_month"].get(month, 0.0) + _number_value(row.get("quantity_sold"))

    series_items: list[ProductMonthlySeries] = []
    for entry in grouped.values():
        revenue = [round(entry["revenue_by_month"].get(month, 0.0), 2) for month in months]
        quantity = [round(entry["quantity_by_month"].get(month, 0.0), 2) for month in months]
        if sum(revenue) <= 0:
            continue
        series_items.append(
            ProductMonthlySeries(
                product_key=entry["product_key"],
                product_id=entry["product_id"],
                product_name=entry["product_name"],
                months=months,
                revenue=revenue,
                quantity=quantity,
            )
        )
    if not series_items:
        raise ProductPredictionInsufficientDataError(
            "At least 1 product sales month with positive revenue is required to train the prediction model."
        )
    return series_items


def build_feature_row(
    *,
    product_key: str,
    product_name: str,
    month: str,
    month_index: int,
    revenue: list[float],
    quantity: list[float],
) -> list[object]:
    return [
        product_key,
        product_name,
        month_index,
        int(month[-2:]),
        _last_value(revenue, 1),
        _last_value(quantity, 1),
        _last_value(revenue, 2),
        _last_value(revenue, 3),
        _last_value(quantity, 2),
        _last_value(quantity, 3),
        _mean(revenue[-3:]),
        _mean(quantity[-3:]),
        sum(revenue[-6:]),
        sum(quantity[-6:]),
        _growth_rate(sum(revenue[-6:]), sum(revenue[-12:-6]) if len(revenue) >= 12 else sum(revenue[:-6])),
        _consistency_score([value for value in revenue if value > 0]),
        sum(1 for value in revenue if value > 0),
    ]


def _state_from_series(series: ProductMonthlySeries) -> dict[str, Any]:
    return {
        "product_key": series.product_key,
        "product_id": series.product_id,
        "product_name": series.product_name,
        "months": series.months,
        "revenue": series.revenue,
        "quantity": series.quantity,
    }


def _series_from_state(raw_state: Any) -> ProductMonthlySeries | None:
    if not isinstance(raw_state, dict):
        return None
    product_key = _string_value(raw_state.get("product_key"))
    product_name = _string_value(raw_state.get("product_name"))
    months = raw_state.get("months")
    revenue = raw_state.get("revenue")
    quantity = raw_state.get("quantity")
    if not product_key or not product_name or not isinstance(months, list) or not isinstance(revenue, list) or not isinstance(quantity, list):
        return None
    if not months or len(months) != len(revenue) or len(months) != len(quantity):
        return None
    product_id = raw_state.get("product_id") if isinstance(raw_state.get("product_id"), int) else None
    return ProductMonthlySeries(
        product_key=product_key,
        product_id=product_id,
        product_name=product_name,
        months=[str(month) for month in months],
        revenue=[_number_value(value) for value in revenue],
        quantity=[_number_value(value) for value in quantity],
    )


def _safe_model_score(pipeline: Any, features: list[list[object]], targets: list[list[float]]) -> float | None:
    if len(features) < 2:
        return None
    try:
        score = float(pipeline.score(features, targets))
    except Exception:
        return None
    return score if math.isfinite(score) else None


def _training_data_quality(dataset: TrainingDataset) -> str:
    if dataset.fallback_training_rows:
        return DATA_QUALITY_LIMITED
    if len(dataset.months) < 3:
        return DATA_QUALITY_LIMITED
    if len(dataset.months) < 12:
        return DATA_QUALITY_SHORT
    return DATA_QUALITY_HISTORICAL


def _limited_data_year_estimate(series: ProductMonthlySeries) -> tuple[float, float]:
    observed_revenue = [value for value in series.revenue if value > 0]
    observed_quantity = [value for value in series.quantity if value > 0]
    if not observed_revenue:
        return 0.0, 0.0

    average_revenue = sum(observed_revenue) / len(observed_revenue)
    average_quantity = sum(observed_quantity) / len(observed_quantity) if observed_quantity else 0.0
    conservative_multiplier = 0.85
    return average_revenue * 12 * conservative_multiplier, average_quantity * 12 * conservative_multiplier


def _prediction_methodology(data_quality: str) -> str:
    if data_quality == DATA_QUALITY_LIMITED:
        return (
            "Limited-data estimate: ranking uses a conservative annualized run-rate from available "
            "Odoo product sales, not a full machine-learning forecast."
        )
    if data_quality == DATA_QUALITY_SHORT:
        return (
            "Short-history forecast: uses the trained scikit-learn RandomForestRegressor model, "
            "but confidence is reduced because fewer than 12 months of sales history are available."
        )
    return (
        "Forecast uses a trained scikit-learn RandomForestRegressor model from the local "
        "product_prediction_model.pkl artifact, using Odoo monthly product sales features."
    )


def _prediction_warning(data_quality: str, metadata: dict[str, Any]) -> str | None:
    months_count = _metadata_int(metadata, "months_count") or 0
    if data_quality == DATA_QUALITY_LIMITED:
        return (
            f"Only {months_count} month(s) of sales history were found, so this is a limited-data "
            "estimate with reduced confidence."
        )
    if data_quality == DATA_QUALITY_SHORT:
        return (
            f"Only {months_count} month(s) of sales history were found; use this as a directional "
            "forecast and retrain after more sales cycles."
        )
    return None


def _metadata_string(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        return None
    if key == "data_quality" and value == "limited_history":
        return DATA_QUALITY_LIMITED
    return value


def _metadata_int(metadata: dict[str, Any], key: str) -> int | None:
    value = metadata.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _import_sklearn() -> dict[str, Any]:
    try:
        from sklearn.compose import ColumnTransformer
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder
    except ImportError as exc:
        raise ProductPredictionDependencyError(
            "scikit-learn is required for product prediction training. Install backend requirements first."
        ) from exc
    return {
        "ColumnTransformer": ColumnTransformer,
        "OneHotEncoder": OneHotEncoder,
        "Pipeline": Pipeline,
        "RandomForestRegressor": RandomForestRegressor,
    }


def _import_joblib() -> Any:
    try:
        import joblib
    except ImportError as exc:
        raise ProductPredictionDependencyError(
            "joblib is required for product prediction model artifacts. Install backend requirements first."
        ) from exc
    return joblib


def _add_months(month: str, offset: int) -> str:
    year = int(month[:4])
    month_number = int(month[-2:])
    month_index = year * 12 + (month_number - 1) + offset
    return f"{month_index // 12:04d}-{month_index % 12 + 1:02d}"


def _growth_rate(recent_revenue: float, previous_revenue: float) -> float:
    if previous_revenue <= 0:
        return 1.0 if recent_revenue > 0 else 0.0
    return (recent_revenue - previous_revenue) / previous_revenue


def _consistency_score(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.35 if values else 0.0
    mean_value = sum(values) / len(values)
    if mean_value <= 0:
        return 0.0
    variance = sum((value - mean_value) ** 2 for value in values) / len(values)
    coefficient = math.sqrt(variance) / mean_value
    return max(0.0, min(1.0, 1.0 - min(coefficient, 1.0)))


def _confidence_score(
    *,
    months_total: int,
    months_observed: int,
    consistency_score: float,
    growth_rate: float,
    data_quality: str,
) -> float:
    history_score = min(months_total / 24, 1.0)
    coverage_score = min(months_observed / 12, 1.0)
    growth_score = 0.6 if growth_rate >= 0 else max(0.0, 0.6 + growth_rate)
    confidence = (
        0.25
        + 0.25 * history_score
        + 0.25 * coverage_score
        + 0.15 * consistency_score
        + 0.10 * growth_score
    )
    if data_quality == DATA_QUALITY_LIMITED:
        confidence = min(confidence * 0.55, 0.35)
    elif data_quality == DATA_QUALITY_SHORT:
        confidence = min(confidence * 0.75, 0.65)
    return max(0.05, min(confidence, 0.95))


def _last_value(values: list[float], position_from_end: int) -> float:
    if len(values) < position_from_end:
        return 0.0
    return values[-position_from_end]


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _number_value(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _string_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
