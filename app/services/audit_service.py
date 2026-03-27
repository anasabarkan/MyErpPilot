"""
Service layer for audit logging.
Provides functions to log events and extract data from requests.
"""

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.repositories import audit_repository
from app.schemas.audit import AuditLogCreateInternal


# ── Context Extraction Helpers ──────────────────────────

def extract_ip_from_request(request: Request) -> Optional[str]:
    """Extract the client IP address from a FastAPI Request."""
    if request.client:
        return request.client.host
    return None

def extract_user_agent_from_request(request: Request) -> Optional[str]:
    """Extract the user-agent string from a FastAPI Request."""
    return request.headers.get("user-agent")


# ── Generic Logging Function ──────────────────────────

def log_event(
    db: Session,
    action: str,
    entity_type: str,
    user_id: Optional[UUID] = None,
    entity_id: Optional[str] = None,
    status: str = "success",
    message: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """
    Log an arbitrary event into the audit logs.
    This function handles its own errors silently to avoid disrupting core business logic.
    """
    try:
        log_data = AuditLogCreateInternal(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            status=status,
            message=message,
            metadata=metadata,
            ip_address=ip_address,
            user_agent=user_agent
        )
        audit_repository.create_log(db, log_data)
    except Exception as e:
        # In a real system, we might log this meta-failure to standard logging (e.g. logging module)
        print(f"Failed to write audit log: {e}")


# ── Specific Logging Functions ──────────────────────────

def log_auth_event(
    db: Session,
    action: str,  # e.g., "login", "signup", "google_login", "token_validation"
    status: str,  # e.g., "success", "failed", "denied"
    user_id: Optional[UUID] = None,
    message: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Log an authentication-related event."""
    log_event(
        db=db,
        action=action,
        entity_type="auth",
        user_id=user_id,
        entity_id=str(user_id) if user_id else None,
        status=status,
        message=message,
        ip_address=ip_address,
        user_agent=user_agent
    )


def log_approval_event(
    db: Session,
    action: str,  # e.g., "create", "approve", "reject"
    approval_id: str,
    user_id: UUID,
    status: str = "success",
    message: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Log an approval workflow event."""
    log_event(
        db=db,
        action=action,
        entity_type="approval_request",
        user_id=user_id,
        entity_id=approval_id,
        status=status,
        message=message,
        ip_address=ip_address,
        user_agent=user_agent
    )


def log_execution_event(
    db: Session,
    action: str,  # e.g., "execute_request"
    approval_id: str,
    user_id: UUID,
    status: str,  # "success" or "failed"
    message: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Log an execution event following an approval."""
    log_event(
        db=db,
        action=action,
        entity_type="approval_request",
        user_id=user_id,
        entity_id=approval_id,
        status=status,
        message=message,
        metadata=metadata,
        ip_address=ip_address,
        user_agent=user_agent
    )


def log_role_event(
    db: Session,
    action: str,  # e.g., "assign_role", "remove_role", "permission_denied"
    user_id: UUID,  # User performing the action (or None if system)
    target_user_id: Optional[UUID] = None, # User being modified
    role_name: Optional[str] = None,
    status: str = "success",
    message: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Log an RBAC-related event."""
    metadata = {"role_name": role_name} if role_name else None
    
    log_event(
        db=db,
        action=action,
        entity_type="rbac",
        user_id=user_id,
        entity_id=str(target_user_id) if target_user_id else None,
        status=status,
        message=message,
        metadata=metadata,
        ip_address=ip_address,
        user_agent=user_agent
    )


def log_ai_event(
    db: Session,
    action: str,  # e.g., "query_submitted", "sql_generated", "action_proposed"
    user_id: UUID,
    status: str = "success",
    message: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Log an AI orchestration event."""
    log_event(
        db=db,
        action=action,
        entity_type="ai",
        user_id=user_id,
        entity_id=None,
        status=status,
        message=message,
        metadata=metadata,
        ip_address=ip_address,
        user_agent=user_agent
    )


def log_sensitive_crud_event(
    db: Session,
    action: str,  # e.g., "create", "update", "delete", "deactivate"
    entity_type: str, # e.g. "user", "role"
    entity_id: str,
    user_id: UUID,
    status: str = "success",
    message: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Log a sensitive CRUD operation."""
    log_event(
        db=db,
        action=action,
        entity_type=entity_type,
        user_id=user_id,
        entity_id=entity_id,
        status=status,
        message=message,
        metadata=metadata,
        ip_address=ip_address,
        user_agent=user_agent
    )
