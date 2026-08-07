"""agentpool_server — backward-compatible shim for wolfharness_server.

This package has been renamed to ``wolfharness_server``. Importing ``agentpool_server``
is deprecated and will be removed in a future release.
"""

from __future__ import annotations

import warnings

from wolfharness_server import *  # noqa: F403

warnings.warn(
    "``agentpool_server`` has been renamed to ``wolfharness_server``. "
    "Update your imports: ``from wolfharness_server import ...``",
    DeprecationWarning,
    stacklevel=2,
)
