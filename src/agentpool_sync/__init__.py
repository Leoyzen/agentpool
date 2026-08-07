"""agentpool_sync — backward-compatible shim for wolfharness_sync.

This package has been renamed to ``wolfharness_sync``. Importing ``agentpool_sync``
is deprecated and will be removed in a future release.
"""

from __future__ import annotations

import warnings

from wolfharness_sync import *  # noqa: F403

warnings.warn(
    "``agentpool_sync`` has been renamed to ``wolfharness_sync``. "
    "Update your imports: ``from wolfharness_sync import ...``",
    DeprecationWarning,
    stacklevel=2,
)
