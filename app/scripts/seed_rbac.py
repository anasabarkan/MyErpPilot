import os
import sys

# Ensure the root of the project is in the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.database.session import SessionLocal
from app.models.role import Role
from app.models.permission import Permission
from app.models.role_permission import RolePermission

DEFAULT_PERMISSIONS = [
    "manage_users",
    "manage_roles",
    "query_data",
    "propose_action",
    "approve_action",
    "execute_action",
    "view_audit_logs",
]

ROLE_PERMISSION_MAPPING = {
    "admin": DEFAULT_PERMISSIONS,
    "manager": ["query_data", "propose_action", "approve_action", "view_audit_logs"],
    "analyst": ["query_data", "propose_action"],
    "operator": ["execute_action"],
    "viewer": ["query_data"],
}

def seed_rbac():
    print("Seeding RBAC roles and permissions...")
    db = SessionLocal()
    
    try:
        # 1. Create permissions
        db_permissions = {}
        for perm_name in DEFAULT_PERMISSIONS:
            perm = db.query(Permission).filter(Permission.name == perm_name).first()
            if not perm:
                perm = Permission(name=perm_name, description=f"Permission to {perm_name.replace('_', ' ')}")
                db.add(perm)
                db.commit()
                db.refresh(perm)
                print(f"Created permission: {perm_name}")
            db_permissions[perm_name] = perm

        # 2. Create roles and mapping
        for role_name, perms in ROLE_PERMISSION_MAPPING.items():
            role = db.query(Role).filter(Role.name == role_name).first()
            if not role:
                role = Role(name=role_name, description=f"{role_name.capitalize()} role")
                db.add(role)
                db.commit()
                db.refresh(role)
                print(f"Created role: {role_name}")

            # Assign permissions to role
            for perm_name in perms:
                perm = db_permissions[perm_name]
                if perm not in role.permissions:
                    role.permissions.append(perm)
            db.commit()
            print(f"Updated permissions for role: {role_name}")

        print("RBAC seeding complete.")
    except Exception as e:
        db.rollback()
        print(f"Error seeding RBAC: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed_rbac()
