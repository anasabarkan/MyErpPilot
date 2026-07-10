"""RBAC seeding script that keeps roles and permissions synchronized."""

from sqlalchemy.orm import Session

from app.core.rbac import DEFAULT_PERMISSIONS, ROLE_PERMISSION_MAPPING
from app.models.permission import Permission
from app.models.role import Role


def seed(db: Session):
    """Seed RBAC roles and permissions."""
    print("  -> Seeding RBAC roles and permissions...")

    db_permissions: dict[str, Permission] = {}
    for perm_name in DEFAULT_PERMISSIONS:
        perm = db.query(Permission).filter(Permission.name == perm_name).first()
        if not perm:
            perm = Permission(
                name=perm_name,
                description=f"Permission to {perm_name.replace('_', ' ')}",
            )
            db.add(perm)
            db.commit()
            db.refresh(perm)
            print(f"    - Created permission: {perm_name}")
        db_permissions[perm_name] = perm

    for role_name, perms in ROLE_PERMISSION_MAPPING.items():
        role = db.query(Role).filter(Role.name == role_name).first()
        if not role:
            role = Role(name=role_name, description=f"{role_name.capitalize()} role")
            db.add(role)
            db.commit()
            db.refresh(role)
            print(f"    - Created role: {role_name}")

        desired_permission_names = set(perms)
        existing_permission_names = {permission.name for permission in role.permissions}
        changed = False

        for permission in list(role.permissions):
            if permission.name not in desired_permission_names:
                role.permissions.remove(permission)
                changed = True

        for perm_name in desired_permission_names:
            permission = db_permissions[perm_name]
            if perm_name not in existing_permission_names:
                role.permissions.append(permission)
                changed = True

        if changed:
            db.commit()
            print(f"    - Synchronized permissions for role: {role_name}")

    print("  -> RBAC seeding complete.")
