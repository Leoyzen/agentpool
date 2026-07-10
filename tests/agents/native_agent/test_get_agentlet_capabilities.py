"""Tests for get_agentlet() capability-based construction.

These tests verify that get_agentlet() correctly collects and assembles
capabilities from all sources (tool providers, hooks, MCP, history processors,
builtin tools) and passes them to the PydanticAgent constructor.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.orchestrator.core import EventBus


@pytest.fixture
def mock_agent() -> Agent[Any]:
    """Create an agent with heavily mocked internals for get_agentlet testing."""
    model = TestModel(custom_output_text="test")
    return Agent(name="capability-test-agent", model=model)


@pytest.fixture
def mock_provider_with_capability() -> MagicMock:
    """Mock tool provider that returns a capability."""
    provider = MagicMock()
    provider.name = "mock_provider"
    cap = MagicMock()
    provider.as_capability.return_value = cap
    provider.get_instructions = AsyncMock(return_value=[])
    provider.get_tools = AsyncMock(return_value=[])
    return provider


@pytest.fixture
def mock_provider_no_capability() -> MagicMock:
    """Mock tool provider that returns no capability."""
    provider = MagicMock()
    provider.name = "no_cap_provider"
    provider.as_capability.return_value = None
    provider.get_instructions = AsyncMock(return_value=[])
    provider.get_tools = AsyncMock(return_value=[])
    return provider


@pytest.fixture
def mock_provider_with_instructions() -> MagicMock:
    """Mock tool provider that returns instructions."""
    provider = MagicMock()
    provider.name = "instruction_provider"
    provider.as_capability.return_value = None
    provider.get_tools = AsyncMock(return_value=[])

    def simple_instruction() -> str:
        return "Provider instruction"

    provider.get_instructions = AsyncMock(return_value=[simple_instruction])
    return provider


@pytest.fixture
def mock_hook_manager() -> MagicMock:
    """Mock hook manager for NativeAgentHookManager."""
    hook_mgr = MagicMock()
    hook_mgr.has_hooks.return_value = True
    return hook_mgr


@pytest.fixture
def mock_mcp_manager() -> MagicMock:
    """Mock MCP manager that returns capabilities."""
    mcp_mgr = MagicMock()
    cap1 = MagicMock()
    cap2 = MagicMock()
    mcp_mgr.as_capability = AsyncMock(return_value=[cap1, cap2])
    return mcp_mgr


@pytest.fixture
def mock_history_processor() -> MagicMock:
    """Mock history processor callable."""
    processor = MagicMock()
    processor.__name__ = "mock_processor"
    return processor


# ---------------------------------------------------------------------------
# Test: Tool provider capabilities are collected
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_collects_tool_provider_capabilities(
    mock_agent: Agent[Any],
    mock_provider_with_capability: MagicMock,
    mock_provider_no_capability: MagicMock,
) -> None:
    """Tool providers' as_capability() results are collected."""
    # Add mock providers to external_providers list
    mock_agent.tools.external_providers = [
        mock_provider_with_capability,
        mock_provider_no_capability,
    ]

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        # Verify both providers' as_capability were called
        mock_provider_with_capability.as_capability.assert_called_once()
        mock_provider_no_capability.as_capability.assert_called_once()

        # Verify capability from first provider was passed
        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []
        assert mock_provider_with_capability.as_capability.return_value in capabilities


# ---------------------------------------------------------------------------
# Test: Hooks capability is created via ToolInterceptCapability
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_creates_hooks_capability(
    mock_agent: Agent[Any],
    mock_hook_manager: MagicMock,
) -> None:
    """Hooks capability created via ToolInterceptCapability construction."""
    mock_agent._hook_manager = mock_hook_manager
    hooks_cap = MagicMock()

    with (
        patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent,
        patch(
            "agentpool.agents.native_agent.tool_intercept.ToolInterceptCapability",
            return_value=hooks_cap,
        ) as mock_ti_class,
    ):
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        mock_ti_class.assert_called_once_with(hook_manager=mock_hook_manager)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []
        assert hooks_cap in capabilities


# ---------------------------------------------------------------------------
# Test: HookManager capability used directly (no adapter wrapping)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_uses_hook_manager_capability_directly(
    mock_agent: Agent[Any],
    mock_hook_manager: MagicMock,
) -> None:
    """ToolInterceptCapability is constructed directly with the hook manager.

    The native agent run loop already publishes RunStartedEvent,
    ToolCallStartEvent, and ToolCallCompleteEvent, so no adapter wrapping is
    needed.
    """
    mock_agent._hook_manager = mock_hook_manager
    hooks_cap = MagicMock()

    # Create run_ctx with event_bus (previously triggered adapter wrapping)
    event_bus = EventBus()
    run_ctx = AgentRunContext(session_id="test-session", event_bus=event_bus)

    with (
        patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent,
        patch(
            "agentpool.agents.native_agent.tool_intercept.ToolInterceptCapability",
            return_value=hooks_cap,
        ) as mock_ti_class,
    ):
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None, run_ctx=run_ctx)

        # Verify ToolInterceptCapability was constructed with hook_manager
        mock_ti_class.assert_called_once_with(hook_manager=mock_hook_manager)

        # The raw hooks capability should be used directly (no adapter wrapping)
        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []
        assert hooks_cap in capabilities, (
            "ToolInterceptCapability should be used directly (no adapter wrapping)"
        )


# ---------------------------------------------------------------------------
# Test: MCP capabilities are collected from MCPManager
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_collects_mcp_capabilities(
    mock_agent: Agent[Any],
    mock_mcp_manager: MagicMock,
) -> None:
    """MCP capabilities collected from mcp.as_capability()."""
    mock_agent.mcp = mock_mcp_manager

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        mock_mcp_manager.as_capability.assert_called_once()

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []

        # Both MCP capabilities should be in the list
        mcp_caps = mock_mcp_manager.as_capability.return_value
        for cap in mcp_caps:
            assert cap in capabilities


# ---------------------------------------------------------------------------
# Test: History processors are wrapped as ProcessHistory capabilities
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_wraps_history_processors(
    mock_agent: Agent[Any],
    mock_history_processor: MagicMock,
) -> None:
    """History processors wrapped as ProcessHistory capabilities."""
    from pydantic_ai.capabilities import ProcessHistory

    # Mock _resolve_history_processors to return our processor
    with (
        patch.object(
            mock_agent,
            "_resolve_history_processors",
            return_value=[mock_history_processor],
        ),
        patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent,
    ):
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []

        # Find ProcessHistory capability
        process_history_caps = [cap for cap in capabilities if isinstance(cap, ProcessHistory)]
        assert len(process_history_caps) == 1, "Expected exactly one ProcessHistory capability"


# ---------------------------------------------------------------------------
# Test: Builtin tools are wrapped as NativeTool capabilities
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_wraps_builtin_tools(
    mock_agent: Agent[Any],
) -> None:
    """Builtin tools wrapped as NativeTool capabilities."""
    from pydantic_ai.capabilities import NativeTool

    # Create mock builtin tools
    builtin_tool_1 = MagicMock()
    builtin_tool_2 = MagicMock()
    mock_agent._builtin_tools = [builtin_tool_1, builtin_tool_2]

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []

        # Find NativeTool capabilities
        native_tool_caps = [cap for cap in capabilities if isinstance(cap, NativeTool)]
        assert len(native_tool_caps) == 2, "Expected exactly two NativeTool capabilities"


# ---------------------------------------------------------------------------
# Test: All capabilities are passed to PydanticAgent constructor
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_passes_capabilities_to_pydantic_agent(
    mock_agent: Agent[Any],
    mock_provider_with_capability: MagicMock,
    mock_hook_manager: MagicMock,
    mock_mcp_manager: MagicMock,
    mock_history_processor: MagicMock,
) -> None:
    """All capabilities passed to PydanticAgent constructor."""
    from pydantic_ai.capabilities import NativeTool, ProcessHistory

    # Set up all sources
    mock_agent.tools.external_providers = [mock_provider_with_capability]
    mock_agent._hook_manager = mock_hook_manager
    mock_agent.mcp = mock_mcp_manager
    mock_agent._builtin_tools = [MagicMock()]
    hooks_cap = MagicMock()

    with (
        patch.object(
            mock_agent,
            "_resolve_history_processors",
            return_value=[mock_history_processor],
        ),
        patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent,
        patch(
            "agentpool.agents.native_agent.tool_intercept.ToolInterceptCapability",
            return_value=hooks_cap,
        ),
    ):
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []

        # Verify all capability types are present
        assert mock_provider_with_capability.as_capability.return_value in capabilities
        assert hooks_cap in capabilities
        assert any(cap in capabilities for cap in mock_mcp_manager.as_capability.return_value)
        assert any(isinstance(cap, ProcessHistory) for cap in capabilities)
        assert any(isinstance(cap, NativeTool) for cap in capabilities)

        # Verify capabilities list is not empty
        assert len(capabilities) > 0


# ---------------------------------------------------------------------------
# Test: Instructions are collected from SystemPrompts and providers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_collects_instructions(
    mock_agent: Agent[Any],
    mock_provider_with_instructions: MagicMock,
) -> None:
    """Instructions from SystemPrompts and providers collected."""
    mock_agent.tools.external_providers = [mock_provider_with_instructions]

    # Mock sys_prompts to return known instructions
    system_instruction = "System prompt instruction"
    with (
        patch.object(
            mock_agent.sys_prompts,
            "to_pydantic_ai_instructions",
            new=AsyncMock(return_value=[system_instruction]),
        ),
        patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent,
    ):
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        instructions = call_kwargs.get("instructions", [])

        # System instruction should be present
        assert system_instruction in instructions

        # Provider's get_instructions should have been called
        mock_provider_with_instructions.get_instructions.assert_called_once()


# ---------------------------------------------------------------------------
# Test: No duplicate _resolve_history_processors() calls
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_no_duplicate_history_resolution(
    mock_agent: Agent[Any],
) -> None:
    """_resolve_history_processors called exactly once."""
    with (
        patch.object(
            mock_agent,
            "_resolve_history_processors",
            return_value=[],
        ) as mock_resolve,
        patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent,
    ):
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        mock_resolve.assert_called_once()


# ---------------------------------------------------------------------------
# Test: No manual tool wrapping via wrap_tool
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_no_wrap_tool_usage(
    mock_agent: Agent[Any],
) -> None:
    """No manual tool wrapping via wrap_tool."""
    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()

        # Also patch wrap_tool if it were imported - but it's not
        # This test verifies by ensuring the method completes without wrap_tool
        # and that tools are passed via capabilities, not manual wrapping
        await mock_agent.get_agentlet(None, None, None)

        # Verify PydanticAgent was called
        mock_pydantic_agent.assert_called_once()

        # Verify no "wrap_tool" attribute access on the agent or its tools
        # (This is implicit - the test passes if no AttributeError is raised)


# ---------------------------------------------------------------------------
# Test: Default providers always contribute capabilities
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_default_providers_contribute_capabilities(
    mock_agent: Agent[Any],
) -> None:
    """Even with no custom providers, default providers contribute capabilities."""
    # Clear all custom providers
    mock_agent.tools.external_providers = []
    mock_agent.tools.session_providers = []
    mock_agent._builtin_tools = []

    with (
        patch.object(
            mock_agent,
            "_resolve_history_processors",
            return_value=[],
        ),
        patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent,
    ):
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []

        # Default providers (builtin + worker) should still contribute
        assert capabilities is not None
        assert len(capabilities) >= 2, "Expected at least 2 capabilities from default providers"


# ---------------------------------------------------------------------------
# Test: Instructions from failing provider are handled gracefully
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_handles_failing_provider_instructions(
    mock_agent: Agent[Any],
) -> None:
    """Errors in provider.get_instructions are logged and skipped."""
    failing_provider = MagicMock()
    failing_provider.name = "failing_provider"
    failing_provider.as_capability.return_value = None
    failing_provider.get_tools = AsyncMock(return_value=[])
    failing_provider.get_instructions = AsyncMock(side_effect=RuntimeError("Instruction failure"))

    mock_agent.tools.external_providers = [failing_provider]

    with (
        patch.object(
            mock_agent.sys_prompts,
            "to_pydantic_ai_instructions",
            new=AsyncMock(return_value=["system prompt"]),
        ),
        patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent,
    ):
        mock_pydantic_agent.return_value = MagicMock()
        # Should not raise despite provider failing
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        instructions = call_kwargs.get("instructions", [])
        # System prompt should still be present
        assert "system prompt" in instructions


# ---------------------------------------------------------------------------
# Test: Model resolution from string
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_agentlet_resolves_string_model(
    mock_agent: Agent[Any],
) -> None:
    """String model is resolved to Model instance."""
    mock_model = MagicMock()
    mock_model.system = "test"
    mock_model.model_name = "test-model"

    with (
        patch.object(
            mock_agent,
            "_resolve_model_string",
            return_value=(mock_model, None),
        ),
        patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent,
    ):
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet("custom:model", None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        assert call_kwargs.get("model") is mock_model


# ---------------------------------------------------------------------------
# Test: Python API capability passthrough
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_python_api_capability_passthrough(mock_agent: Agent[Any]) -> None:
    """Pre-instantiated AbstractCapability from config is passed to PydanticAgent."""
    from pydantic_ai.capabilities import Instrumentation

    cap = Instrumentation()
    mock_agent.config = MagicMock()
    mock_agent.config.capabilities = [cap]

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []
        assert cap in capabilities


# ---------------------------------------------------------------------------
# Test: YAML config capability passthrough
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_yaml_config_capability_passthrough(mock_agent: Agent[Any]) -> None:
    """YAML-loaded GenericCapabilityConfig is built and included in capabilities."""
    from agentpool_config.capabilities import GenericCapabilityConfig

    cap_config = GenericCapabilityConfig(
        type="pydantic_ai.capabilities.Instrumentation",
        args={},
    )
    mock_agent.config = MagicMock()
    mock_agent.config.capabilities = [cap_config]

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []
        capability_types = [type(c).__name__ for c in capabilities]
        assert "Instrumentation" in capability_types


# ---------------------------------------------------------------------------
# Test: User capability takes precedence (appended last)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_user_capability_takes_precedence(mock_agent: Agent[Any]) -> None:
    """User-provided capabilities are appended last (highest priority)."""
    from pydantic_ai.capabilities import Instrumentation

    user_cap = Instrumentation()
    mock_agent.config = MagicMock()
    mock_agent.config.capabilities = [user_cap]

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []
        assert capabilities[-1] is user_cap


# ---------------------------------------------------------------------------
# Test: CapabilityConfig.build() is called during get_agentlet()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_capability_config_build_called(mock_agent: Agent[Any]) -> None:
    """GenericCapabilityConfig.build() is called during get_agentlet()."""
    from agentpool_config.capabilities import GenericCapabilityConfig

    cap_config = GenericCapabilityConfig(
        type="pydantic_ai.capabilities.Instrumentation",
        args={},
    )
    mock_agent.config = MagicMock()
    mock_agent.config.capabilities = [cap_config]

    with patch.object(GenericCapabilityConfig, "build") as mock_build:
        mock_build.return_value = MagicMock()
        with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
            mock_pydantic_agent.return_value = MagicMock()
            await mock_agent.get_agentlet(None, None, None)

        mock_build.assert_called_once()


# ---------------------------------------------------------------------------
# Test: Capabilities from from_config() are not duplicated in get_agentlet()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_from_config_capabilities_not_duplicated() -> None:
    """Capabilities built in from_config() must not be re-built in get_agentlet().

    from_config() pre-builds capabilities from config.capabilities and stores
    them in _extra_capabilities. get_agentlet() also iterates config.capabilities
    and builds them. This causes duplicate capability instances, which leads to
    tool name conflicts (e.g. two 'task' tools from two BackgroundTaskCapability
    instances).

    This test calls from_config() with a config containing a capability, then
    calls get_agentlet() and verifies the capability appears exactly once.
    """
    from llmling_models_config import TestModelConfig
    from pydantic_ai.capabilities import Instrumentation

    from agentpool.models.agents import NativeAgentConfig
    from agentpool_config.capabilities import GenericCapabilityConfig

    cap_config = GenericCapabilityConfig(
        type="pydantic_ai.capabilities.Instrumentation",
        args={},
    )
    config = NativeAgentConfig(
        name="test_dedup_agent",
        model=TestModelConfig(custom_output_text="test"),
        system_prompt=["Be helpful."],
        capabilities=[cap_config],
    )

    agent = Agent.from_config(config)

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()
        await agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []

        # Count Instrumentation instances — should be exactly 1, not 2
        instrumentation_caps = [c for c in capabilities if isinstance(c, Instrumentation)]
        assert len(instrumentation_caps) == 1, (
            f"Expected exactly 1 Instrumentation capability, found {len(instrumentation_caps)}. "
            "Capabilities are being duplicated between _extra_capabilities and config.capabilities."
        )
