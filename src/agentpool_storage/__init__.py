"""agentpool_storage — backward-compatible shim for wolfharness_storage.

This package has been renamed to ``wolfharness_storage``. Importing ``agentpool_storage``
is deprecated and will be removed in a future release.
"""

from __future__ import annotations

import warnings

from wolfharness_storage import *  # noqa: F403

warnings.warn(
    "``agentpool_storage`` has been renamed to ``wolfharness_storage``. "
    "Update your imports: ``from wolfharness_storage import ...``",
    DeprecationWarning,
    stacklevel=2,
)
