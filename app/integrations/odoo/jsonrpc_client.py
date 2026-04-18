"""
JSON-RPC client for Odoo.
"""

from __future__ import annotations

from uuid import uuid4

import httpx


class OdooConnectionError(Exception):
    """Raised when Odoo cannot be reached."""


class OdooAuthenticationError(Exception):
    """Raised when Odoo credentials are invalid."""


class OdooRequestError(Exception):
    """Raised when Odoo returns an application-level JSON-RPC error."""


class OdooJsonRpcClient:
    """
    Minimal JSON-RPC client for authenticating and listing models.
    """

    def __init__(
        self,
        *,
        base_url: str,
        database_name: str,
        username: str,
        api_key: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.database_name = database_name
        self.username = username
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _call(self, service: str, method: str, args: list[object]) -> object:
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "service": service,
                "method": method,
                "args": args,
            },
            "id": str(uuid4()),
        }

        try:
            response = httpx.post(
                f"{self.base_url}/jsonrpc",
                json=payload,
                timeout=self.timeout_seconds,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OdooConnectionError(
                "Unable to reach Odoo server. Check URL/network and try again."
            ) from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise OdooRequestError("Odoo returned an invalid JSON response.") from exc

        if body.get("error"):
            error_payload = body["error"]
            message = (
                error_payload.get("data", {}).get("message")
                or error_payload.get("message")
                or "Unknown Odoo error."
            )
            raise OdooRequestError(f"Odoo request failed: {message}")

        return body.get("result")

    def authenticate(self) -> int:
        result = self._call(
            "common",
            "authenticate",
            [self.database_name, self.username, self.api_key, {}],
        )
        if not result:
            raise OdooAuthenticationError("Invalid Odoo credentials.")

        if not isinstance(result, int):
            raise OdooRequestError("Unexpected Odoo authentication response.")

        return result

    def execute_kw(
        self,
        *,
        user_id: int,
        model: str,
        method: str,
        args: list[object] | None = None,
        kwargs: dict[str, object] | None = None,
    ) -> object:
        return self._call(
            "object",
            "execute_kw",
            [
                self.database_name,
                user_id,
                self.api_key,
                model,
                method,
                args or [],
                kwargs or {},
            ],
        )

    def search_count(self, *, user_id: int, model: str, domain: list[object] | None = None) -> int:
        result = self.execute_kw(
            user_id=user_id,
            model=model,
            method="search_count",
            args=[domain or []],
        )
        if not isinstance(result, int):
            raise OdooRequestError(f"Unexpected search_count response for model '{model}'.")
        return result

    def search_read(
        self,
        *,
        user_id: int,
        model: str,
        domain: list[object] | None = None,
        fields: list[str] | None = None,
        limit: int = 80,
        order: str | None = None,
    ) -> list[dict[str, object]]:
        kwargs: dict[str, object] = {"fields": fields or [], "limit": limit}
        if order:
            kwargs["order"] = order

        result = self.execute_kw(
            user_id=user_id,
            model=model,
            method="search_read",
            args=[domain or []],
            kwargs=kwargs,
        )
        if not isinstance(result, list):
            raise OdooRequestError(f"Unexpected search_read response for model '{model}'.")
        return [row for row in result if isinstance(row, dict)]

    def list_models(self, user_id: int) -> list[dict[str, str]]:
        result = self.search_read(
            user_id=user_id,
            model="ir.model",
            domain=[],
            fields=["model", "name"],
            limit=5000,
            order="model asc",
        )

        models: list[dict[str, str]] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            model = item.get("model")
            name = item.get("name")
            if isinstance(model, str) and isinstance(name, str):
                models.append({"model": model, "name": name})

        return models
