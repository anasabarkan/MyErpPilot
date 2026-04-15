from cryptography.fernet import Fernet
import pytest

from app.core import security


@pytest.fixture(autouse=True)
def clear_fernet_cache():
    security._odoo_fernet.cache_clear()
    yield
    security._odoo_fernet.cache_clear()


def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setattr(
        security.settings,
        "ODOO_CREDENTIALS_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    encrypted = security.encrypt_secret("odoo-api-key")

    assert encrypted != "odoo-api-key"
    assert security.decrypt_secret(encrypted) == "odoo-api-key"


def test_encrypt_secret_fails_with_invalid_key(monkeypatch):
    monkeypatch.setattr(
        security.settings,
        "ODOO_CREDENTIALS_ENCRYPTION_KEY",
        "invalid-key",
    )

    with pytest.raises(RuntimeError):
        security.encrypt_secret("odoo-api-key")


def test_decrypt_secret_fails_for_corrupted_token(monkeypatch):
    monkeypatch.setattr(
        security.settings,
        "ODOO_CREDENTIALS_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )

    with pytest.raises(ValueError):
        security.decrypt_secret("not-a-valid-token")

