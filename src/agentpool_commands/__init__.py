"""agentpool_commands — backward-compatible shim for wolfharness_commands.

This package has been renamed to ``wolfharness_commands``. Importing ``agentpool_commands``
is deprecated and will be removed in a future release.
"""

from __future__ import annotations

import warnings

from wolfharness_commands import *  # noqa: F403

warnings.warn(
    "``agentpool_commands`` has been renamed to ``wolfharness_commands``. "
    "Update your imports: ``from wolfharness_commands import ...``",
    DeprecationWarning,
    stacklevel=2,
)
