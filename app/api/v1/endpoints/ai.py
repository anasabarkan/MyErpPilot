"""
AI query endpoint for natural language ERP analytics.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_permission
from app.database.session import get_db
from app.integrations.odoo import OdooAuthenticationError, OdooConnectionError, OdooRequestError
from app.models.user import User
from app.schemas.ai import AIQueryRequest, AIQueryResponse
from app.services import audit_service
from app.services.ai_orchestrator_service import (
    AIIntentNotImplementedError,
    AIOrchestratorService,
    AIUnsupportedQuestionError,
)
from app.services.odoo_query_service import (
    OdooQueryCredentialError,
    OdooQueryExecutionError,
    OdooQuerySourceNotConfiguredError,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/query",
    response_model=AIQueryResponse,
    dependencies=[Depends(require_permission("query_data"))],
)
def query_ai(
    payload: AIQueryRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    orchestrator = AIOrchestratorService(db)
    ip_address = audit_service.extract_ip_from_request(request)
    user_agent = audit_service.extract_user_agent_from_request(request)

    audit_service.log_ai_event(
        db,
        action="query_submitted",
        user_id=current_user.id,
        status="success",
        metadata={"question": payload.question},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    try:
        result = orchestrator.query(question=payload.question, user_id=current_user.id)
    except OdooQuerySourceNotConfiguredError as exc:
        _log_ai_failure(
            db=db,
            current_user=current_user,
            question=payload.question,
            message=str(exc),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except (AIUnsupportedQuestionError, AIIntentNotImplementedError) as exc:
        _log_ai_failure(
            db=db,
            current_user=current_user,
            question=payload.question,
            message=str(exc),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except (OdooQueryCredentialError, OdooAuthenticationError) as exc:
        _log_ai_failure(
            db=db,
            current_user=current_user,
            question=payload.question,
            message=str(exc),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except (OdooQueryExecutionError, OdooRequestError, OdooConnectionError) as exc:
        _log_ai_failure(
            db=db,
            current_user=current_user,
            question=payload.question,
            message=str(exc),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Odoo query failed: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - keep endpoint errors controlled and JSON-safe
        logger.exception("ai_query_unhandled_error")
        _log_ai_failure(
            db=db,
            current_user=current_user,
            question=payload.question,
            message="Unexpected error while processing AI query.",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error while processing AI query.",
        ) from exc

    audit_service.log_ai_event(
        db,
        action="query_processed",
        user_id=current_user.id,
        status="success",
        metadata={
            "question": result.question,
            "intent": result.intent.value,
            "handler": result.handler,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return result


def _log_ai_failure(
    *,
    db: Session,
    current_user: User,
    question: str,
    message: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    audit_service.log_ai_event(
        db,
        action="query_processed",
        user_id=current_user.id,
        status="failed",
        message=message,
        metadata={"question": question},
        ip_address=ip_address,
        user_agent=user_agent,
    )
