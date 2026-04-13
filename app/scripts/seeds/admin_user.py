"""
Bootstrap seed for the default admin account.

Creates the admin user only if it does not already exist, then ensures
the account has the ``admin`` role assigned.
"""

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models.role import Role
from app.models.user import User


def seed(db: Session):
    """Ensure the configured admin account exists and has the admin role."""
    print("  -> Seeding bootstrap admin account...")

    admin_user = db.query(User).filter(User.email == settings.ADMIN_SEED_EMAIL).first()
    if not admin_user:
        admin_user = User(
            email=settings.ADMIN_SEED_EMAIL,
            full_name=settings.ADMIN_SEED_FULL_NAME,
            hashed_password=hash_password(settings.ADMIN_SEED_PASSWORD),
            provider="local",
            is_active=True,
            is_verified=True,
        )
        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)
        print(f"    - Created admin user: {admin_user.email}")

    admin_role = db.query(Role).filter(Role.name == "admin").first()
    if not admin_role:
        admin_role = Role(name="admin", description="Administrator role")
        db.add(admin_role)
        db.commit()
        db.refresh(admin_role)
        print("    - Created missing 'admin' role placeholder")

    changed = False

    if not admin_user.is_active:
        admin_user.is_active = True
        changed = True

    if "local" != admin_user.provider:
        admin_user.provider = "local"
        changed = True

    if admin_role not in admin_user.roles:
        admin_user.roles.append(admin_role)
        changed = True

    if changed:
        db.commit()
        db.refresh(admin_user)
        print(f"    - Ensured admin access for: {admin_user.email}")

    print("  -> Bootstrap admin account ready.")
