"""
RBAC seeding script — defines default roles and permissions.
"""

from sqlalchemy.orm import Session
from app.models.role import Role
from app.models.permission import Permission

DEFAULT_PERMISSIONS = [
    "manage_users",
    "view_users",
    "manage_roles",
    "query_data",
    "propose_action",
    "approve_action",
    "execute_action",
    "view_audit_logs",
    "manage_data_sources",
    "view_data_sources",
    "test_data_source",
]

ROLE_PERMISSION_MAPPING = {
    "admin": DEFAULT_PERMISSIONS,
    "manager": [
        "view_users",
        "query_data",
        "propose_action",
        "approve_action",
        "view_audit_logs",
        "view_data_sources",
    ],
    "analyst": [
        "query_data",
        "propose_action",
        "manage_data_sources",
        "view_data_sources",
        "test_data_source",
    ],
    "operator": ["execute_action"],
    "viewer": ["query_data", "view_data_sources"],
}

def seed(db: Session):
    """Seed RBAC roles and permissions."""
    print("  -> Seeding RBAC roles and permissions...")
    
    # 1. Create permissions
    db_permissions = {}
    for perm_name in DEFAULT_PERMISSIONS:
        perm = db.query(Permission).filter(Permission.name == perm_name).first()
        if not perm:
            perm = Permission(name=perm_name, description=f"Permission to {perm_name.replace('_', ' ')}")
            db.add(perm)
            db.commit()
            db.refresh(perm)
            print(f"    - Created permission: {perm_name}")
        db_permissions[perm_name] = perm

    # 2. Create roles and mapping
    for role_name, perms in ROLE_PERMISSION_MAPPING.items():
        role = db.query(Role).filter(Role.name == role_name).first()
        if not role:
            role = Role(name=role_name, description=f"{role_name.capitalize()} role")
            db.add(role)
            db.commit()
            db.refresh(role)
            print(f"    - Created role: {role_name}")

        # Assign permissions to role
        changed = False
        for perm_name in perms:
            perm = db_permissions[perm_name]
            if perm not in role.permissions:
                role.permissions.append(perm)
                changed = True
        if changed:
            db.commit()
            print(f"    - Updated permissions for role: {role_name}")

    print("  -> RBAC seeding complete.")
