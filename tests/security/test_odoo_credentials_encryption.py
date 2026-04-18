from cryptography.fernet import Fernet
import pytest

from app.core import security


@pytest.fixture(autouse=True)
def clear_fernet_cache():
    security._odoo_fernet.cache_clear()
    yield
    security._odoo_fernet.cache_clear()


def _set_keys(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fernet_key: str = "",
    encryption_key: str = "",
    legacy_key: str = "",
    auto_generate: bool = False,
) -> None:
    monkeypatch.setattr(security.settings, "FERNET_KEY", fernet_key)
    monkeypatch.setattr(security.settings, "ENCRYPTION_KEY", encryption_key)
    monkeypatch.setattr(security.settings, "ODOO_CREDENTIALS_ENCRYPTION_KEY", legacy_key)
    monkeypatch.setattr(security.settings, "AUTO_GENERATE_FERNET_KEY", auto_generate)


def test_encrypt_decrypt_roundtrip(monkeypatch):
    _set_keys(monkeypatch, fernet_key=Fernet.generate_key().decode())
    encrypted = security.encrypt_secret("odoo-api-key")

    assert encrypted != "odoo-api-key"
    assert security.decrypt_secret(encrypted) == "odoo-api-key"


def test_encrypt_uses_encryption_key_fallback(monkeypatch):
    _set_keys(monkeypatch, encryption_key=Fernet.generate_key().decode())
    encrypted = security.encrypt_secret("odoo-api-key")

    assert encrypted != "odoo-api-key"
    assert security.decrypt_secret(encrypted) == "odoo-api-key"


def test_encrypt_secret_fails_with_invalid_key(monkeypatch):
    _set_keys(monkeypatch, fernet_key="invalid-key")

    with pytest.raises(RuntimeError):
        security.encrypt_secret("odoo-api-key")


def test_encrypt_secret_fails_with_missing_key(monkeypatch):
    _set_keys(monkeypatch)

    with pytest.raises(RuntimeError, match="FERNET_KEY"):
        security.encrypt_secret("odoo-api-key")


def test_decrypt_secret_fails_for_corrupted_token(monkeypatch):
    _set_keys(monkeypatch, fernet_key=Fernet.generate_key().decode())

    with pytest.raises(ValueError):
        security.decrypt_secret("not-a-valid-token")
