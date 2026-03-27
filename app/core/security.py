"""
Security utilities: password hashing and JWT token management.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# ── Password hashing ────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """Return a bcrypt hash of *plain_password*."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return ``True`` if *plain_password* matches *hashed_password*."""
    return pwd_context.verify(plain_password, hashed_password)


# ── JWT tokens ───────────────────────────────────

def create_access_token(user_id: UUID, expires_delta: timedelta | None = None) -> str:
    """
    Create a signed JWT access token for the given *user_id*.

    If *expires_delta* is ``None`` the token expires after
    ``ACCESS_TOKEN_EXPIRE_MINUTES`` from settings.
    """
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """
    Decode and validate a JWT token.

    Returns the payload dict on success, or ``None`` if the token is
    invalid / expired.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None
