"""agentpool — backward-compatible shim for wolfharness.

This package has been renamed to ``wolfharness``. Importing ``agentpool``
is deprecated and will be removed in a future release.
"""

from __future__ import annotations

import warnings
from importlib.metadata import version

from wolfharness import *  # noqa: F403

warnings.warn(
    "``agentpool`` has been renamed to ``wolfharness``. "
    "Update your imports: ``from wolfharness import ...``",
    DeprecationWarning,
    stacklevel=2,
)

__version__ = version("wolfharness")
