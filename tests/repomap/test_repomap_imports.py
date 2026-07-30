"""Import-level smoke tests for repomap submodules.

Verifies that each repomap module can be imported without ImportError.
Does not call any repomap functions.
"""

from __future__ import annotations

import importlib

import pytest


pytestmark = pytest.mark.unit


REPOMAP_MODULES = [
    "agentwolf.repomap.context",
    "agentwolf.repomap.core",
    "agentwolf.repomap.languages",
    "agentwolf.repomap.outline",
    "agentwolf.repomap.tags",
    "agentwolf.repomap.types",
    "agentwolf.repomap.utils",
]


@pytest.mark.parametrize("module_name", REPOMAP_MODULES)
def test_repomap_importable(module_name: str) -> None:
    importlib.import_module(module_name)
