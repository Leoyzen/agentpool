"""agentpool_config — backward-compatible shim for wolfharness_config.

This package has been renamed to ``wolfharness_config``. Importing ``agentpool_config``
is deprecated and will be removed in a future release.
"""

from __future__ import annotations

import warnings

from wolfharness_config import *  # noqa: F403

warnings.warn(
    "``agentpool_config`` has been renamed to ``wolfharness_config``. "
    "Update your imports: ``from wolfharness_config import ...``",
    DeprecationWarning,
    stacklevel=2,
)
