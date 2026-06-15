from datetime import datetime, timezone

import pytest

from app.services.product_prediction_service import (
    ProductPredictionInsufficientDataError,
    ProductPredictionModelMissingError,
    ProductPredictionService,
    build_training_dataset,
    train_product_prediction_model,
)


def _history_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    start_year = datetime.now(timezone.utc).year - 1
    for index in range(24):
        year = start_year + index // 12
        month = index % 12 + 1
        month_key = f"{year}-{month:02d}"
        rows.append(
            {
                "product_id": 10,
                "product_name": "Laptop",
                "month": month_key,
                "revenue": 2000.0 + index * 180.0,
                "quantity_sold": 3.0 + index * 0.5,
            }
        )
        rows.append(
            {
                "product_id": 11,
                "product_name": "Mouse",
                "month": month_key,
                "revenue": 300.0 + index * 10.0,
                "quantity_sold": 10.0,
            }
        )
    return rows


def _single_product_history(
    *,
    product_id: int,
    product_name: str,
    start_year: int,
    months: int,
    monthly_revenue: float,
    monthly_quantity: float = 1.0,
) -> list[dict[str, object]]:
    return [
        {
            "product_id": product_id,
            "product_name": product_name,
            "month": f"{start_year + index // 12}-{index % 12 + 1:02d}",
            "revenue": monthly_revenue,
            "quantity_sold": monthly_quantity,
        }
        for index in range(months)
    ]


def test_build_training_dataset_creates_features_and_targets():
    dataset = build_training_dataset(_history_rows())

    assert len(dataset.features) > 0
    assert len(dataset.features) == len(dataset.targets)
    assert len(dataset.features[0]) == 17
    assert dataset.features[0][0] == "10"
    assert dataset.targets[0][0] > 0
    assert len(dataset.product_states) == 2


def test_train_product_prediction_model_writes_artifact_and_metadata(artifact_tmp_path):
    model_path = artifact_tmp_path / "product_prediction_model.pkl"
    metadata_path = artifact_tmp_path / "product_prediction_model.meta.json"

    metadata = train_product_prediction_model(
        history=_history_rows(),
        model_path=model_path,
        metadata_path=metadata_path,
    )

    assert model_path.exists()
    assert metadata_path.exists()
    assert metadata["model_version"] == "product_prediction_model_v1"
    assert metadata["training_rows"] > 0


def test_prediction_service_loads_pkl_and_predicts_best_product(artifact_tmp_path):
    model_path = artifact_tmp_path / "product_prediction_model.pkl"
    train_product_prediction_model(history=_history_rows(), model_path=model_path)
    service = ProductPredictionService(model_path=model_path)

    result = service.predict_best_product(target_year=datetime.now(timezone.utc).year + 1)

    assert result.best_product.product_name == "Laptop"
    assert result.best_product.predicted_revenue > 0
    assert result.model_version == "product_prediction_model_v1"
    assert result.training_rows is not None


def test_prediction_service_missing_pkl_raises_clean_error(artifact_tmp_path):
    service = ProductPredictionService(model_path=artifact_tmp_path / "missing.pkl")

    with pytest.raises(ProductPredictionModelMissingError):
        service.predict_best_product(target_year=datetime.now(timezone.utc).year + 1)


def test_training_accepts_one_month_history(artifact_tmp_path):
    history = [
        {
            "product_id": 10,
            "product_name": "Laptop",
            "month": "2026-01",
            "revenue": 1000.0,
            "quantity_sold": 2.0,
        },
        {
            "product_id": 11,
            "product_name": "Mouse",
            "month": "2026-01",
            "revenue": 700.0,
            "quantity_sold": 8.0,
        },
    ]
    dataset = build_training_dataset(history)
    model_path = artifact_tmp_path / "product_prediction_model.pkl"

    metadata = train_product_prediction_model(history=history, model_path=model_path)
    service = ProductPredictionService(model_path=model_path)
    result = service.predict_best_product(target_year=datetime.now(timezone.utc).year + 1)

    assert len(dataset.features) == 2
    assert len(dataset.targets) == 2
    assert metadata["fallback_training_rows"] == 2
    assert metadata["data_quality"] == "limited_data"
    assert result.best_product.product_name == "Laptop"
    assert result.best_product.predicted_revenue > 0
    assert result.data_quality == "limited_data"
    assert result.warning is not None
    assert result.confidence <= 0.35
    assert "Limited data warning" in result.analysis[0]


def test_two_month_history_returns_limited_low_confidence_prediction(artifact_tmp_path):
    year = datetime.now(timezone.utc).year
    history = [
        *_single_product_history(
            product_id=10,
            product_name="A Product",
            start_year=year,
            months=2,
            monthly_revenue=500.0,
        ),
        *_single_product_history(
            product_id=11,
            product_name="B Product",
            start_year=year,
            months=2,
            monthly_revenue=800.0,
        ),
    ]
    model_path = artifact_tmp_path / "product_prediction_model.pkl"
    metadata = train_product_prediction_model(history=history, model_path=model_path)

    result = ProductPredictionService(model_path=model_path).predict_best_product(target_year=year + 1)

    assert metadata["data_quality"] == "limited_data"
    assert result.best_product.product_name == "B Product"
    assert result.best_product.confidence <= 0.35
    assert result.warning is not None


def test_twelve_month_history_returns_historical_forecast(artifact_tmp_path):
    year = datetime.now(timezone.utc).year - 1
    history = [
        *_single_product_history(
            product_id=10,
            product_name="A Product",
            start_year=year,
            months=12,
            monthly_revenue=500.0,
        ),
        *_single_product_history(
            product_id=11,
            product_name="B Product",
            start_year=year,
            months=12,
            monthly_revenue=800.0,
        ),
    ]
    model_path = artifact_tmp_path / "product_prediction_model.pkl"
    metadata = train_product_prediction_model(history=history, model_path=model_path)

    result = ProductPredictionService(model_path=model_path).predict_best_product(target_year=year + 1)

    assert metadata["data_quality"] == "historical"
    assert result.data_quality == "historical"
    assert result.warning is None
    assert result.best_product.product_name == "B Product"


def test_target_year_forecast_sums_exact_requested_year_months(artifact_tmp_path):
    year = datetime.now(timezone.utc).year
    history = _single_product_history(
        product_id=10,
        product_name="Laptop",
        start_year=year,
        months=12,
        monthly_revenue=100.0,
        monthly_quantity=2.0,
    )
    model_path = artifact_tmp_path / "product_prediction_model.pkl"
    train_product_prediction_model(history=history, model_path=model_path)

    result = ProductPredictionService(model_path=model_path).predict_best_product(target_year=year)

    assert result.best_product.predicted_revenue == 1200.0
    assert result.best_product.predicted_quantity == 24.0


def test_model_diagnostics_reports_limited_artifact(artifact_tmp_path):
    model_path = artifact_tmp_path / "product_prediction_model.pkl"
    train_product_prediction_model(
        history=[
            {
                "product_id": 10,
                "product_name": "Laptop",
                "month": "2026-01",
                "revenue": 1000.0,
                "quantity_sold": 2.0,
            }
        ],
        model_path=model_path,
    )

    diagnostics = ProductPredictionService(model_path=model_path).diagnose_model()

    assert diagnostics.status == "limited_data"
    assert diagnostics.warning is not None
    assert diagnostics.months_count == 1


def test_training_requires_at_least_one_positive_sales_month():
    with pytest.raises(ProductPredictionInsufficientDataError):
        build_training_dataset([])
