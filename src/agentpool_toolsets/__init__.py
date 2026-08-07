"""agentpool_toolsets — backward-compatible shim for wolfharness_toolsets.

This package has been renamed to ``wolfharness_toolsets``. Importing ``agentpool_toolsets``
is deprecated and will be removed in a future release.
"""

from __future__ import annotations

import warnings

from wolfharness_toolsets import *  # noqa: F403

warnings.warn(
    "``agentpool_toolsets`` has been renamed to ``wolfharness_toolsets``. "
    "Update your imports: ``from wolfharness_toolsets import ...``",
    DeprecationWarning,
    stacklevel=2,
)
