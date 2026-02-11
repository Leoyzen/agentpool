"""Context variable management for config path resolution.

This module provides context-aware path resolution using ContextVars,
allowing config-relative paths to work correctly regardless of CWD.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from contextvars import ContextVar
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from contextvars import Token
    from types import TracebackType
    from typing import Self

    from upathtools import JoinablePathLike, UPath
else:
    from upathtools import UPath


# Context variable storing the current config directory.
# This is set during manifest loading to enable config-relative path resolution.
CONFIG_DIR: ContextVar[UPath | None] = ContextVar("config_dir", default=None)


class ConfigContextManager(AbstractContextManager["ConfigContextManager"]):
    """Context manager for setting config directory during manifest loading.

    This context manager temporarily sets the CONFIG_DIR context variable,
    enabling config-relative path resolution for all Pydantic models using
    ConfigPath fields.

    Example:
        >>> with ConfigContextManager("/path/to/config.yml"):
        ...     manifest = AgentsManifest.model_validate(yaml_data)
        ...     # All ConfigPath fields resolve relative to config directory
    """

    def __init__(self, config_path: JoinablePathLike | None) -> None:
        """Initialize with a config file path.

        Args:
            config_path: Path to the configuration file (or directory).
                If a file path, the parent directory is used as config dir.
                If None, no context is set (paths resolve to CWD).
        """
        self._config_dir: UPath | None = None
        self._token: Token[UPath | None] | None = None

        if config_path is not None:
            path = UPath(config_path)
            # If path points to a file, use its parent directory
            # Otherwise use the path itself as config directory
            self._config_dir = path.parent if path.suffix else path

    def __enter__(self) -> Self:
        """Enter the context and set CONFIG_DIR."""
        if self._config_dir is not None:
            self._token = CONFIG_DIR.set(self._config_dir)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the context and reset CONFIG_DIR."""
        if self._token is not None:
            CONFIG_DIR.reset(self._token)
