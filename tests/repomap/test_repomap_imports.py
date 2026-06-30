"""Import-level smoke tests for repomap submodules.

Verifies that each repomap module can be imported without ImportError.
Does not call any repomap functions.
"""

from __future__ import annotations

import importlib

import pytest


REPOMAP_MODULES = [
    "agentpool.repomap.context",
    "agentpool.repomap.core",
    "agentpool.repomap.languages",
    "agentpool.repomap.outline",
    "agentpool.repomap.tags",
    "agentpool.repomap.types",
    "agentpool.repomap.utils",
]


@pytest.mark.parametrize("module_name", REPOMAP_MODULES)
def test_repomap_importable(module_name: str) -> None:
    importlib.import_module(module_name)
