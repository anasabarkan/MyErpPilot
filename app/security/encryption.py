"""
Fernet-based encryption utilities for sensitive values.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


@lru_cache(maxsize=1)
def get_fernet_cipher() -> Fernet:
    """
    Build and cache a Fernet cipher from configured environment keys.
    """
    try:
        key = settings.get_fernet_key()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return Fernet(key.encode("utf-8"))


def encrypt_value(value: str) -> str:
    """
    Encrypt a plain-text value for persistent storage.
    """
    if not value:
        raise ValueError("Value to encrypt cannot be empty.")

    encrypted = get_fernet_cipher().encrypt(value.encode("utf-8"))
    return encrypted.decode("utf-8")


def decrypt_value(value: str) -> str:
    """
    Decrypt a previously encrypted value.
    """
    if not value:
        raise ValueError("Encrypted value cannot be empty.")

    try:
        decrypted = get_fernet_cipher().decrypt(value.encode("utf-8"))
    except InvalidToken as exc:
        raise ValueError(
            "Encrypted credential is invalid, corrupted, or encrypted with a different Fernet key."
        ) from exc

    return decrypted.decode("utf-8")
