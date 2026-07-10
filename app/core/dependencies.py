"""
FastAPI dependencies for authentication and RBAC authorization.
"""

from __future__ import annotations

from collections.abc import Iterable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.rbac import (
    ADMIN_ONLY_ROLES,
    ANALYST_OR_ADMIN_ROLES,
    MANAGER_OR_ADMIN_ROLES,
    OPERATOR_OR_ADMIN_ROLES,
)
from app.core.security import decode_access_token
from app.database.session import get_db
from app.models.user import User
from app.services import audit_service
from app.services.role_service import get_user_permissions_list
from app.services.user_service import get_user_by_id

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Decode the JWT bearer token and return the corresponding User.

    Raises ``401`` if the token is invalid, expired, or the user
    does not exist.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    user = get_user_by_id(db, user_id)
    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated.",
        )

    return user


def _ensure_verified_user(current_user: User) -> None:
    if getattr(current_user, "is_verified", True) is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required before accessing the web app.",
        )


def get_current_user_role_names(current_user: User) -> set[str]:
    role_names: set[str] = set()
    for role in getattr(current_user, "roles", []) or []:
        if hasattr(role, "name"):
            role_names.add(str(role.name))
        elif isinstance(role, str):
            role_names.add(role)
    return role_names


def _log_access_denied(
    db: Session,
    *,
    current_user: User | None,
    reason: str,
    required_roles: Iterable[str] | None = None,
    required_permissions: Iterable[str] | None = None,
) -> None:
    metadata: dict[str, object] = {"reason": reason}
    if required_roles is not None:
        metadata["required_roles"] = list(required_roles)
    if required_permissions is not None:
        metadata["required_permissions"] = list(required_permissions)

    audit_service.log_event(
        db=db,
        action="access_denied",
        entity_type="rbac",
        entity_id="rbac_policy",
        user_id=getattr(current_user, "id", None),
        status="denied",
        message=reason,
        metadata=metadata,
    )


def _require_roles(
    *,
    current_user: User,
    db: Session,
    role_names: Iterable[str],
) -> User:
    _ensure_verified_user(current_user)
    required_roles = tuple(role_names)
    user_role_names = get_current_user_role_names(current_user)
    if any(role_name in user_role_names for role_name in required_roles):
        return current_user

    reason = f"Not enough role privileges. Required one of: {', '.join(required_roles)}"
    _log_access_denied(
        db,
        current_user=current_user,
        reason=reason,
        required_roles=required_roles,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=reason,
    )


def require_admin_user(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    return _require_roles(current_user=current_user, db=db, role_names=ADMIN_ONLY_ROLES)


def require_manager_or_admin_user(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    return _require_roles(current_user=current_user, db=db, role_names=MANAGER_OR_ADMIN_ROLES)


def require_operator_or_admin_user(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    return _require_roles(current_user=current_user, db=db, role_names=OPERATOR_OR_ADMIN_ROLES)


def require_analyst_or_admin_user(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    return _require_roles(current_user=current_user, db=db, role_names=ANALYST_OR_ADMIN_ROLES)


def require_permission(permission_name: str):
    """
    Dependency factory that checks if the current user has the required permission.
    Raises an HTTP 403 Forbidden exception if the user does not have it.
    """

    def permission_dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        _ensure_verified_user(current_user)
        permissions = set(get_user_permissions_list(db, user_id=current_user.id))
        if permission_name not in permissions:
            reason = f"Not enough permissions. Required: {permission_name}"
            _log_access_denied(
                db,
                current_user=current_user,
                reason=reason,
                required_permissions=(permission_name,),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=reason,
            )
        return current_user

    return permission_dependency


def require_any_permission(*permission_names: str):
    """
    Dependency factory that grants access when the current user has at least one
    of the provided permissions.
    """

    def permission_dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        _ensure_verified_user(current_user)
        permissions = set(get_user_permissions_list(db, user_id=current_user.id))
        if not any(permission_name in permissions for permission_name in permission_names):
            required = ", ".join(permission_names)
            reason = f"Not enough permissions. Required one of: {required}"
            _log_access_denied(
                db,
                current_user=current_user,
                reason=reason,
                required_permissions=permission_names,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=reason,
            )
        return current_user

    return permission_dependency
