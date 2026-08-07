"""agentpool_bot — backward-compatible shim for wolfharness_bot.

This package has been renamed to ``wolfharness_bot``. Importing ``agentpool_bot``
is deprecated and will be removed in a future release.
"""

from __future__ import annotations

import warnings

from wolfharness_bot import *  # noqa: F403

warnings.warn(
    "``agentpool_bot`` has been renamed to ``wolfharness_bot``. "
    "Update your imports: ``from wolfharness_bot import ...``",
    DeprecationWarning,
    stacklevel=2,
)
