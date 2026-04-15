"""
Odoo integration helpers.
"""

from .jsonrpc_client import (
    OdooAuthenticationError,
    OdooConnectionError,
    OdooJsonRpcClient,
    OdooRequestError,
)

__all__ = [
    "OdooJsonRpcClient",
    "OdooConnectionError",
    "OdooAuthenticationError",
    "OdooRequestError",
]

