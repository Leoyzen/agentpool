"""agentpool_prompts — backward-compatible shim for wolfharness_prompts.

This package has been renamed to ``wolfharness_prompts``. Importing ``agentpool_prompts``
is deprecated and will be removed in a future release.
"""

from __future__ import annotations

import warnings

from wolfharness_prompts import *  # noqa: F403

warnings.warn(
    "``agentpool_prompts`` has been renamed to ``wolfharness_prompts``. "
    "Update your imports: ``from wolfharness_prompts import ...``",
    DeprecationWarning,
    stacklevel=2,
)
