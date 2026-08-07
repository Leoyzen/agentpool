"""agentpool_cli — backward-compatible shim for wolfharness_cli.

This package has been renamed to ``wolfharness_cli``. Importing ``agentpool_cli``
is deprecated and will be removed in a future release.
"""

from __future__ import annotations

import warnings

from wolfharness_cli import *  # noqa: F403

warnings.warn(
    "``agentpool_cli`` has been renamed to ``wolfharness_cli``. "
    "Update your imports: ``from wolfharness_cli import ...``",
    DeprecationWarning,
    stacklevel=2,
)
