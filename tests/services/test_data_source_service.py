from datetime import datetime, timezone
from uuid import uuid4

from app.models.data_source import DataSource
from app.schemas.data_source import DataSourceUpdate
from app.services import data_source_service as service


def _sample_data_source() -> DataSource:
    return DataSource(
        id=uuid4(),
        name="Odoo Prod",
        odoo_url="http://localhost:8069",
        database_name="odoo",
        username="admin",
        api_key_encrypted="encrypted-token",
        created_by=uuid4(),
        is_active=False,
        models_cache=[{"model": "res.partner", "name": "Contact"}],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def test_get_models_returns_cache_when_refresh_false():
    source = _sample_data_source()

    models = service.get_data_source_models(db=None, data_source=source, refresh=False)  # type: ignore[arg-type]

    assert len(models) == 1
    assert models[0]["model"] == "res.partner"


def test_get_models_refreshes_cache(monkeypatch):
    source = _sample_data_source()
    source.models_cache = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        service,
        "_test_and_get_models_for_data_source",
        lambda _source: [{"model": "sale.order", "name": "Sales Order"}],
    )

    def fake_update_data_source(_db, _db_obj, update_data):
        captured["update_data"] = update_data
        _db_obj.models_cache = update_data["models_cache"]
        return _db_obj

    monkeypatch.setattr(service.repo, "update_data_source", fake_update_data_source)

    models = service.get_data_source_models(db=object(), data_source=source, refresh=True)

    assert len(models) == 1
    assert captured["update_data"]["models_cache"][0]["model"] == "sale.order"  # type: ignore[index]


def test_update_data_source_encrypts_api_key(monkeypatch):
    source = _sample_data_source()
    update_payload = DataSourceUpdate(api_key="new-key")
    captured: dict[str, object] = {}

    monkeypatch.setattr(service, "encrypt_secret", lambda _value: "encrypted-new-key")

    def fake_update_data_source(_db, db_obj, update_data):
        captured["update_data"] = update_data
        db_obj.api_key_encrypted = update_data["api_key_encrypted"]
        return db_obj

    monkeypatch.setattr(service.repo, "update_data_source", fake_update_data_source)

    updated = service.update_data_source(db=object(), db_obj=source, obj_in=update_payload)

    assert updated.api_key_encrypted == "encrypted-new-key"
    assert captured["update_data"]["api_key_encrypted"] == "encrypted-new-key"  # type: ignore[index]

