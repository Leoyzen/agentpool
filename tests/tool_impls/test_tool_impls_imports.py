"""Import-level smoke tests for all tool_impls submodules.

Verifies that each tool module can be imported without ImportError or
unexpected side effects. Does not call any tool functions.
"""

from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.unit


TOOL_IMPLS_MODULES = [
    "agentpool.tool_impls.agent_cli",
    "agentpool.tool_impls.bash",
    "agentpool.tool_impls.delete_path",
    "agentpool.tool_impls.download_file",
    "agentpool.tool_impls.execute_code",
    "agentpool.tool_impls.grep",
    "agentpool.tool_impls.list_directory",
    "agentpool.tool_impls.question",
    "agentpool.tool_impls.read",
]


@pytest.mark.parametrize("module_name", TOOL_IMPLS_MODULES)
def test_tool_impls_importable(module_name: str) -> None:
    importlib.import_module(module_name)
