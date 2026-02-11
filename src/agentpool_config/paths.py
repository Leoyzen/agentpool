"""Config path resolution utilities.

Provides the ConfigPath type and resolve_config_path function for
config-relative path resolution with environment variable overrides
and backward compatibility.
"""

from __future__ import annotations

import os
from typing import Annotated

from pydantic import BeforeValidator
from upathtools import UPath

from agentpool_config.context import CONFIG_DIR


# Environment variable names
CONFIG_DIR_ENV_VAR = "AGENTPOOL_CONFIG_DIR"
LEGACY_PATHS_ENV_VAR = "AGENTPOOL_LEGACY_PATHS"


def resolve_config_path(path: str | UPath) -> UPath:
    """Resolve a config path relative to config directory.

    Resolution order:
    1. If AGENTPOOL_LEGACY_PATHS=1: return path unchanged (relative to CWD)
    2. If AGENTPOOL_CONFIG_DIR env var set: resolve relative to that
    3. If CONFIG_DIR context var set: resolve relative to that
    4. Otherwise: resolve relative to CWD (path as-is)

    Absolute paths are always returned unchanged.

    Args:
        path: The path to resolve (string or UPath).

    Returns:
        UPath: The resolved path.

    Example:
        >>> # Legacy mode (returns path unchanged)
        >>> with setenv(AGENTPOOL_LEGACY_PATHS="1"):
        ...     resolve_config_path("./foo")  # UPath("./foo")

        >>> # With CONFIG_DIR set via context
        >>> with ConfigContextManager("/config/agents.yml"):
        ...     resolve_config_path("./foo")  # UPath("/config/foo")

        >>> # With AGENTPOOL_CONFIG_DIR env var
        >>> with setenv(AGENTPOOL_CONFIG_DIR="/custom"):
        ...     resolve_config_path("./foo")  # UPath("/custom/foo")
    """
    upath = UPath(path)

    # Absolute paths are never resolved
    if upath.is_absolute():
        return upath

    # 1. Legacy mode: return path unchanged (relative to CWD)
    if os.environ.get(LEGACY_PATHS_ENV_VAR) == "1":
        return upath

    # 2. Environment variable override
    config_dir_env = os.environ.get(CONFIG_DIR_ENV_VAR)
    if config_dir_env:
        return UPath(config_dir_env) / upath

    # 3. Context variable
    config_dir_ctx = CONFIG_DIR.get()
    if config_dir_ctx is not None:
        return config_dir_ctx / upath

    # 4. Default: return as-is (relative to CWD)
    return upath


# Pydantic type alias for config-relative paths.
# Use this as a field type to enable automatic path resolution.
ConfigPath = Annotated[UPath, BeforeValidator(resolve_config_path)]
"""Type alias for config-relative paths with automatic resolution.

This type can be used in Pydantic models to automatically resolve
paths relative to the config file location:

    class MyConfig(Schema):
        data_path: ConfigPath  # Resolves relative to config dir

Example:
    >>> with ConfigContextManager("/home/user/project/config.yml"):
    ...     config = MyConfig(data_path="./data")
    ...     str(config.data_path)  # "/home/user/project/data"
"""
