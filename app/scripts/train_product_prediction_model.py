"""
Train and persist the product prediction model artifact.

Usage:
    python -m app.scripts.train_product_prediction_model
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.core.config import settings
from app.database.session import SessionLocal
from app.services.odoo_query_service import (
    OdooQueryCredentialError,
    OdooQueryExecutionError,
    OdooQuerySourceNotConfiguredError,
    OdooQueryService,
)
from app.services.product_prediction_service import (
    ProductPredictionDependencyError,
    ProductPredictionInsufficientDataError,
    ProductPredictionService,
    train_product_prediction_model,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the product prediction .pkl model from Odoo sales data.")
    parser.add_argument(
        "--model-path",
        default=settings.PREDICTION_MODEL_PATH,
        help="Output .pkl model path. Defaults to PREDICTION_MODEL_PATH.",
    )
    parser.add_argument(
        "--metadata-path",
        default=None,
        help="Output metadata JSON path. Defaults to model path with .meta.json suffix.",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=settings.PREDICTION_HISTORY_MONTHS,
        help="Number of historical months to fetch from Odoo.",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Inspect the current prediction model artifact without retraining.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.diagnose:
        diagnostics = ProductPredictionService(model_path=args.model_path).diagnose_model()
        print(f"Prediction model status: {diagnostics.status}")
        print(f"Model path: {diagnostics.model_path}")
        if diagnostics.model_version:
            print(f"Model version: {diagnostics.model_version}")
        if diagnostics.trained_at:
            print(f"Trained at: {diagnostics.trained_at}")
        if diagnostics.data_quality:
            print(f"Data quality: {diagnostics.data_quality}")
        if diagnostics.months_count is not None:
            print(f"Months count: {diagnostics.months_count}")
        if diagnostics.products_count is not None:
            print(f"Products count: {diagnostics.products_count}")
        if diagnostics.training_rows is not None:
            print(f"Training rows: {diagnostics.training_rows}")
        if diagnostics.fallback_training_rows is not None:
            print(f"Fallback training rows: {diagnostics.fallback_training_rows}")
        if diagnostics.warning:
            print(f"Warning: {diagnostics.warning}")
        return 0 if diagnostics.status != "unusable" else 1

    db = SessionLocal()
    try:
        query_service = OdooQueryService(db)
        runtime = query_service.build_runtime_context()
        history = query_service.get_product_sales_history(runtime, months=args.months)
        metadata = train_product_prediction_model(
            history=history,
            model_path=Path(args.model_path),
            metadata_path=Path(args.metadata_path) if args.metadata_path else None,
            history_months=args.months,
        )
    except (
        OdooQuerySourceNotConfiguredError,
        OdooQueryCredentialError,
        OdooQueryExecutionError,
        ProductPredictionDependencyError,
        ProductPredictionInsufficientDataError,
    ) as exc:
        print(f"Product prediction training failed: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    print(
        "Product prediction model trained: "
        f"{metadata['training_rows']} rows, "
        f"{metadata['products_count']} products, "
        f"{metadata['first_month']}..{metadata['last_month']}"
    )
    print(f"Model path: {Path(args.model_path)}")
    print(f"Metadata path: {Path(args.metadata_path) if args.metadata_path else Path(args.model_path).with_suffix('.meta.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
