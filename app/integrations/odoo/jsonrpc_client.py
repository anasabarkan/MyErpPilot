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

    def list_models(self, user_id: int) -> list[dict[str, str]]:
        result = self._call(
            "object",
            "execute_kw",
            [
                self.database_name,
                user_id,
                self.api_key,
                "ir.model",
                "search_read",
                [[]],
                {"fields": ["model", "name"], "order": "model asc"},
            ],
        )

        if not isinstance(result, list):
            raise OdooRequestError("Unexpected Odoo model list response.")

        models: list[dict[str, str]] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            model = item.get("model")
            name = item.get("name")
            if isinstance(model, str) and isinstance(name, str):
                models.append({"model": model, "name": name})

        return models

