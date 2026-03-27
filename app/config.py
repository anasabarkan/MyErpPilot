"""
Backward-compatible re-export.

All settings now live in ``app.core.config``.  Existing imports of
``from app.config import settings`` continue to work.
"""

from app.core.config import Settings, settings  # noqa: F401
