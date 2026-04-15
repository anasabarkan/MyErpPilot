"""
Security utilities: password hashing and JWT token management.
"""

from functools import lru_cache
from datetime import datetime, timedelta, timezone
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
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


@lru_cache(maxsize=1)
def _odoo_fernet() -> Fernet:
    """
    Build a Fernet instance from the configured Odoo encryption key.
    """
    raw_key = settings.ODOO_CREDENTIALS_ENCRYPTION_KEY.strip()
    if not raw_key:
        raise RuntimeError(
            "ODOO_CREDENTIALS_ENCRYPTION_KEY is not configured. "
            "Set a valid Fernet key in the environment."
        )

    try:
        return Fernet(raw_key.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - surface a sanitized configuration error
        raise RuntimeError(
            "ODOO_CREDENTIALS_ENCRYPTION_KEY is invalid. "
            "It must be a URL-safe base64-encoded 32-byte key."
        ) from exc


def encrypt_secret(secret: str) -> str:
    """
    Encrypt a secret for storage.
    """
    if not secret:
        raise ValueError("Secret cannot be empty.")

    token = _odoo_fernet().encrypt(secret.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_secret(encrypted_secret: str) -> str:
    """
    Decrypt a previously encrypted secret.
    """
    if not encrypted_secret:
        raise ValueError("Encrypted secret cannot be empty.")

    try:
        value = _odoo_fernet().decrypt(encrypted_secret.encode("utf-8"))
    except InvalidToken as exc:
        raise ValueError("Encrypted credential is invalid or corrupted.") from exc

    return value.decode("utf-8")
