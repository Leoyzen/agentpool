"""Compatibility shim for the removed ``acp.settings`` module.

This module provides a minimal ``get_settings()`` function that returns a
settings object with the attributes the codebase expects.  The original
module was removed during refactoring, but several source files still import
``from acp.settings import get_settings``.

Will be removed in a future version once all call-sites are updated.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import os


class ProtocolVersion(IntEnum):
    """ACP protocol version supported by this installation."""

    V1 = 1
    V2 = 2


@dataclass(frozen=True)
class ACPSettings:
    """Minimal settings object for ACP protocol configuration."""

    _protocol_version: int | None = None

    def get_protocol_version(self) -> ProtocolVersion:
        """Return the configured ACP protocol version.

        If ``ACP_PROTOCOL_VERSION`` environment variable is set, use that.
        Otherwise fall back to the ``PROTOCOL_VERSION`` constant from
        ``acp.schema`` (currently ``1``).
        """
        if self._protocol_version is not None:
            return ProtocolVersion(self._protocol_version)

        env_val = os.environ.get("ACP_PROTOCOL_VERSION")
        if env_val is not None:
            return ProtocolVersion(int(env_val))

        # Import lazily to avoid circular imports at module load time.
        from acp.schema import PROTOCOL_VERSION

        return ProtocolVersion(PROTOCOL_VERSION)


def get_settings() -> ACPSettings:
    """Return the global ACP settings singleton.

    Returns:
        An ``ACPSettings`` instance with protocol version configuration.
    """
    return _SETTINGS


_SETTINGS = ACPSettings()


__all__ = ["ACPSettings", "ProtocolVersion", "get_settings"]
