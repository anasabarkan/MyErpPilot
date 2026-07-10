"""Shared RBAC constants for backend authorization and seeding."""

from __future__ import annotations

ADMIN_ROLE = "admin"
MANAGER_ROLE = "manager"
ANALYST_ROLE = "analyst"
OPERATOR_ROLE = "operator"
VIEWER_ROLE = "viewer"

APP_ROLES = (
    ADMIN_ROLE,
    MANAGER_ROLE,
    ANALYST_ROLE,
    OPERATOR_ROLE,
    VIEWER_ROLE,
)

ADMIN_ONLY_ROLES = (ADMIN_ROLE,)
MANAGER_OR_ADMIN_ROLES = (ADMIN_ROLE, MANAGER_ROLE)
OPERATOR_OR_ADMIN_ROLES = (ADMIN_ROLE, OPERATOR_ROLE)
ANALYST_OR_ADMIN_ROLES = (ADMIN_ROLE, ANALYST_ROLE)

DEFAULT_PERMISSIONS = [
    "manage_users",
    "view_users",
    "manage_roles",
    "query_data",
    "propose_action",
    "approve_action",
    "execute_action",
    "view_audit_logs",
    "manage_odoo_sources",
    "manage_data_sources",
    "view_data_sources",
    "test_data_source",
]

ROLE_PERMISSION_MAPPING = {
    ADMIN_ROLE: DEFAULT_PERMISSIONS,
    MANAGER_ROLE: [
        "query_data",
        "propose_action",
        "approve_action",
        "view_audit_logs",
    ],
    ANALYST_ROLE: [
        "query_data",
        "propose_action",
        "manage_data_sources",
        "view_data_sources",
        "test_data_source",
    ],
    OPERATOR_ROLE: [
        "query_data",
        "execute_action",
    ],
    VIEWER_ROLE: [
        "query_data",
    ],
}
