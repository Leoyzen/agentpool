"""Test configuration and shared fixtures."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.test import TestModel
import pytest
import yamling

from agentpool import Agent, AgentPool, AgentsManifest, NativeAgentConfig


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


TEST_RESPONSE = "I am a test response"


@pytest.fixture
def default_model() -> str:
    """Default model for testing."""
    return os.getenv("TEST_DEFAULT_MODEL") or "openai-chat:svc/glm-4.7"


@pytest.fixture
def vision_model() -> str:
    """Vision-capable model for testing."""
    return os.getenv("TEST_VISION_MODEL") or "openai-chat:svc/kimi-k2"


@pytest.fixture(scope="session", autouse=True)
def unset_anthropic_api_key():
    os.environ["ANTHROPIC_API_KEY"] = ""


@pytest.fixture(autouse=True)
def _patch_toolset_lifecycle(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """Patch MCPToolset.__aenter__/__aexit__ to avoid real MCP connections.

    ``get_capabilities()`` eagerly enters MCPToolset instances on cache miss
    (issue #175 fix). Without this patch, every test calling
    ``get_capabilities()`` would try to spawn subprocesses or open network
    connections.

    The fake implementations track ``_running_count`` exactly like the real
    pydantic-ai MCPToolset, so tests can verify reference-counting behaviour
    without real connections.

    Tests that need the real MCPToolset can opt out with
    ``@pytest.mark.real_mcp``.
    """
    if request.node.get_closest_marker("real_mcp"):
        yield
        return

    original_aenter = MCPToolset.__aenter__
    original_aexit = MCPToolset.__aexit__

    async def fake_aenter(self):
        self._running_count += 1
        return self

    async def fake_aexit(self, exc_type, exc_val, exc_tb):
        if self._running_count > 0:
            self._running_count -= 1

    MCPToolset.__aenter__ = fake_aenter  # type: ignore[assignment]
    MCPToolset.__aexit__ = fake_aexit  # type: ignore[assignment]
    try:
        yield
    finally:
        MCPToolset.__aenter__ = original_aenter  # type: ignore[assignment]
        MCPToolset.__aexit__ = original_aexit  # type: ignore[assignment]


@pytest.fixture(scope="session", autouse=True)
def disable_logfire(tmp_path_factory):
    """Disable logfire for all tests and set up test directories."""
    from pathlib import Path

    # Set environment variable to disable logfire
    os.environ["LOGFIRE_DISABLE"] = "true"
    # Also disable observability entirely
    os.environ["OBSERVABILITY_ENABLED"] = "false"
    # Skip config dir override in CI - not needed and credentials aren't available anyway
    is_ci = os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")
    if not is_ci:
        # Use temp directory for Claude storage during tests
        claude_test_dir = tmp_path_factory.mktemp("claude_config")
        # Copy credentials file if it exists so integration tests can authenticate
        # Use copy instead of symlink for cross-platform compatibility (Windows needs admin/dev
        # mode for symlinks)
        real_creds = Path.home() / ".claude" / ".credentials.json"
        if real_creds.exists():
            import shutil

            test_creds = claude_test_dir / ".credentials.json"
            shutil.copy2(real_creds, test_creds)
        os.environ["CLAUDE_CONFIG_DIR"] = str(claude_test_dir)
        # Use temp directory for Codex data during tests
        codex_test_dir = tmp_path_factory.mktemp("codex_home")
        # Copy Codex auth file if it exists so integration tests can authenticate
        real_codex_auth = Path.home() / ".codex" / "auth.json"
        if real_codex_auth.exists():
            import shutil

            test_codex_auth = codex_test_dir / "auth.json"
            shutil.copy2(real_codex_auth, test_codex_auth)
        os.environ["CODEX_HOME"] = str(codex_test_dir)

    # Mock logfire configure to be a no-op
    try:
        import logfire

        original_configure = logfire.configure
        logfire.configure = lambda *args, **kwargs: None  # type: ignore
        yield
        logfire.configure = original_configure
    except ImportError:
        # logfire not available, nothing to disable
        yield


VALID_CONFIG = """\
responses:
  SupportResult:
    response_schema:
        type: inline
        description: Support agent response
        fields:
            advice:
                type: str
                description: Support advice
            risk:
                type: int
                ge: 0
                le: 100
  ResearchResult:
    response_schema:
        type: inline
        description: Research agent response
        fields:
            findings:
                type: str
                description: Research findings

agents:
  support:
    type: native
    display_name: Support Agent
    model: {default_model}
    output_type: SupportResult
    system_prompt:
      - You are a support agent
      - "Context: {{data}}"
  researcher:
    type: native
    display_name: Research Agent
    model: {default_model}
    output_type: ResearchResult
    system_prompt: You are a researcher
"""


@pytest.fixture
def valid_config(default_model: str) -> dict[str, Any]:
    """Fixture providing valid agent configuration."""
    return yamling.load_yaml(VALID_CONFIG.format(default_model=default_model), verify_type=dict)


@pytest.fixture
def test_agent() -> Agent[None]:
    """Create an agent with TestModel for testing."""
    model = TestModel(custom_output_text=TEST_RESPONSE)
    return Agent(name="test-agent", model=model)


@pytest.fixture
def manifest():
    """Create test manifest with some agents."""
    agent_1 = NativeAgentConfig(name="agent1", model="test")
    agent_2 = NativeAgentConfig(name="agent2", model="test")
    return AgentsManifest(agents={"agent1": agent_1, "agent2": agent_2})


@pytest.fixture
async def pool(manifest):
    """Create test pool with agents."""
    async with AgentPool(manifest) as pool:
        yield pool


# Model override mapping for custom endpoints without gpt-4o access.
# Tests that hardcode "openai:gpt-4o" or "openai:gpt-4o-mini" are
# transparently remapped to a model available on the custom endpoint.
_DEFAULT_REMAP = os.getenv("TEST_MODEL_OVERRIDE", "openai:gpt-5-nano")
_MODEL_REMAP = {
    "openai:gpt-4o": _DEFAULT_REMAP,
    "openai:gpt-4o-mini": _DEFAULT_REMAP,
}


@pytest.fixture(scope="session", autouse=True)
def remap_hardcoded_test_models():
    """Remap hardcoded gpt-4o/gpt-4o-mini to a custom-available model.

    Controlled via the ``TEST_MODEL_OVERRIDE`` environment variable.
    """
    from unittest.mock import patch

    from agentpool.utils import model_helpers

    original = model_helpers.infer_model

    def _patched_infer(model):
        if isinstance(model, str) and model in _MODEL_REMAP:
            return original(_MODEL_REMAP[model])
        return original(model)

    with (
        patch.object(model_helpers, "infer_model", _patched_infer),
    ):
        yield


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the ``--run-real-models`` CLI flag for live model tests."""
    parser.addoption(
        "--run-real-models",
        action="store_true",
        default=False,
        help="Run live model tests that make real API calls",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers for live model tests."""
    config.addinivalue_line(
        "markers",
        "run_real_models: marks tests that require real model API calls",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-skip credential-dependent and thinking-incompatible tests.

    - ``requires_openai_key``: skipped when ``OPENAI_API_KEY`` is not set
    - ``incompatible_with_thinking``: skipped when ``TEST_DEFAULT_MODEL``
      points to a thinking-mode model (deepseek, kimi) — see issue #84
    """
    _thinking_model_prefixes = ("deepseek", "kimi", "moonshot")

    model = os.getenv("TEST_DEFAULT_MODEL", "")
    is_thinking_model = any(p in model for p in _thinking_model_prefixes)

    for item in items:
        if "requires_openai_key" in item.keywords and not os.environ.get("OPENAI_API_KEY"):
            item.add_marker(
                pytest.mark.skip(
                    reason="OPENAI_API_KEY not set — skipping credential-dependent test",
                )
            )
        if "incompatible_with_thinking" in item.keywords and is_thinking_model:
            item.add_marker(
                pytest.mark.skip(
                    reason=f"TEST_DEFAULT_MODEL='{model}' uses thinking mode — "
                    "structured output (tool_choice: 'required') not supported (issue #84)",
                )
            )
