import httpx
import pytest

from app.integrations.odoo import (
    OdooAuthenticationError,
    OdooConnectionError,
    OdooJsonRpcClient,
    OdooRequestError,
)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("POST", "http://example.com"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


def _build_client() -> OdooJsonRpcClient:
    return OdooJsonRpcClient(
        base_url="http://localhost:8069",
        database_name="odoo",
        username="admin",
        api_key="token",
    )


def test_authenticate_and_list_models_success(monkeypatch):
    calls = {"count": 0}

    def fake_post(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse({"result": 7})
        return FakeResponse(
            {
                "result": [
                    {"model": "res.partner", "name": "Contact"},
                    {"model": "sale.order", "name": "Sales Order"},
                ]
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = _build_client()
    user_id = client.authenticate()
    models = client.list_models(user_id)

    assert user_id == 7
    assert len(models) == 2
    assert models[0]["model"] == "res.partner"


def test_authenticate_raises_for_invalid_credentials(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: FakeResponse({"result": False}))
    client = _build_client()

    with pytest.raises(OdooAuthenticationError):
        client.authenticate()


def test_call_raises_connection_error_on_network_failure(monkeypatch):
    def raise_connect_error(*_args, **_kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "post", raise_connect_error)
    client = _build_client()

    with pytest.raises(OdooConnectionError):
        client.authenticate()


def test_call_raises_request_error_when_odoo_returns_error(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: FakeResponse(
            {
                "error": {
                    "message": "Odoo Server Error",
                    "data": {"message": "Access denied"},
                }
            }
        ),
    )
    client = _build_client()

    with pytest.raises(OdooRequestError):
        client.authenticate()

