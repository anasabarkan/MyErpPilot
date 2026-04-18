"""
Dashboard endpoints powered by active Odoo source data.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_permission
from app.database.session import get_db
from app.integrations.odoo import OdooAuthenticationError, OdooConnectionError, OdooRequestError
from app.models.user import User
from app.schemas.dashboard import DashboardDiagnosticsResponse, DashboardSummaryResponse
from app.services import dashboard_service
from app.services.dashboard_service import DashboardCredentialError, DashboardSourceNotConfiguredError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/overview",
    response_model=DashboardSummaryResponse,
    dependencies=[Depends(require_permission("query_data"))],
    include_in_schema=False,
)
@router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    dependencies=[Depends(require_permission("query_data"))],
)
def get_dashboard_summary(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> Any:
    try:
        return dashboard_service.get_dashboard_summary(db)
    except DashboardSourceNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except DashboardCredentialError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except OdooAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Odoo authentication failed for the active source.",
        ) from exc
    except OdooConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to connect to Odoo using the active source.",
        ) from exc
    except OdooRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Odoo request failed while building dashboard summary: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - keep endpoint errors controlled and JSON-safe
        logger.exception("dashboard_summary_unhandled_error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error while fetching dashboard summary.",
        ) from exc


@router.get(
    "/diagnostics",
    response_model=DashboardDiagnosticsResponse,
    dependencies=[Depends(require_permission("query_data"))],
)
def get_dashboard_diagnostics(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> Any:
    try:
        return dashboard_service.get_dashboard_diagnostics(db)
    except DashboardSourceNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except DashboardCredentialError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except OdooAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Odoo authentication failed for the active source.",
        ) from exc
    except OdooConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to connect to Odoo using the active source.",
        ) from exc
    except OdooRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Odoo request failed while running dashboard diagnostics: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - keep endpoint errors controlled and JSON-safe
        logger.exception("dashboard_diagnostics_unhandled_error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error while fetching dashboard diagnostics.",
        ) from exc
