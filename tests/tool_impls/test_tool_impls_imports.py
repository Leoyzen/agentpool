"""Import-level smoke tests for all tool_impls submodules.

Verifies that each tool module can be imported without ImportError or
unexpected side effects. Does not call any tool functions.
"""

from __future__ import annotations

import importlib

import pytest


pytestmark = pytest.mark.unit


TOOL_IMPLS_MODULES = [
    "agentwolf.tool_impls.agent_cli",
    "agentwolf.tool_impls.bash",
    "agentwolf.tool_impls.delete_path",
    "agentwolf.tool_impls.download_file",
    "agentwolf.tool_impls.execute_code",
    "agentwolf.tool_impls.grep",
    "agentwolf.tool_impls.list_directory",
    "agentwolf.tool_impls.question",
    "agentwolf.tool_impls.read",
]


@pytest.mark.parametrize("module_name", TOOL_IMPLS_MODULES)
def test_tool_impls_importable(module_name: str) -> None:
    importlib.import_module(module_name)
