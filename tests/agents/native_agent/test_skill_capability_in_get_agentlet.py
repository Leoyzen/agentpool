"""Tests for SkillCapability integration in get_agentlet().

These tests verify that SkillCapability instances appear in the capability
chain at the correct position (after MCP, before ProcessHistory) and are
correctly omitted when conditions warrant (no skills, disabled skills,
filtered skills).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.skills.capability import SkillCapability


@pytest.fixture
def mock_agent() -> Agent[Any]:
    """Create an agent with TestModel for get_agentlet testing."""
    model = TestModel(custom_output_text="test")
    return Agent(name="skill-cap-test-agent", model=model)


@pytest.fixture
def mock_mcp_manager() -> MagicMock:
    """Mock MCP manager that returns capabilities."""
    mcp_mgr = MagicMock()
    cap1 = MagicMock()
    cap2 = MagicMock()
    mcp_mgr.get_capabilities = AsyncMock(return_value=[cap1, cap2])
    return mcp_mgr


@pytest.fixture
def mock_history_processor() -> MagicMock:
    """Mock history processor callable."""
    processor = MagicMock()
    processor.__name__ = "mock_processor"
    return processor


def _make_mock_skill(name: str, *, disable_model_invocation: bool = False) -> MagicMock:
    """Create a mock Skill with the given attributes."""
    skill = MagicMock()
    skill.name = name
    skill.disable_model_invocation = disable_model_invocation
    skill.tools = None
    skill.mcp_servers = None
    skill.allowed_tools = None
    skill.load_instructions.return_value = "skill instructions"
    return skill


# ---------------------------------------------------------------------------
# Test: SkillCapability is included at the correct position
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_skill_capability_in_position_after_mcp_before_history(
    mock_agent: Agent[Any],
    mock_mcp_manager: MagicMock,
    mock_history_processor: MagicMock,
) -> None:
    """SkillCapability appears after MCP capabilities and before ProcessHistory."""
    from agentpool.skills.capability import SkillCapability

    # Set up MCP manager
    mock_agent.mcp = mock_mcp_manager

    # Set up agent_pool with pre-built SkillCapability instances
    sk1 = _make_mock_skill("skill-1")
    sk2 = _make_mock_skill("skill-2")
    cap1 = SkillCapability(sk1)
    cap2 = SkillCapability(sk2)
    mock_pool = MagicMock()
    mock_pool.skill_capabilities = [cap1, cap2]
    # Set up host_context chain: agent.host_context → get_context() → ctx.pool → mock_pool
    mock_ctx = MagicMock()
    mock_ctx.pool = mock_pool
    mock_pool.get_context.return_value = mock_ctx
    mock_agent.agent_pool = mock_pool

    # Set up history processor
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

        # Find SkillCapabilities
        skill_caps = [cap for cap in capabilities if isinstance(cap, SkillCapability)]
        assert len(skill_caps) == 2, f"Expected 2 SkillCapability instances, got {len(skill_caps)}"

        # MCP caps are MagicMock instances — track via identity
        mcp_caps = mock_mcp_manager.get_capabilities.return_value
        mcp_indices = [capabilities.index(c) for c in mcp_caps]

        # Find ProcessHistory index
        process_history_indices = [
            i for i, cap in enumerate(capabilities) if isinstance(cap, ProcessHistory)
        ]

        # Find SkillCapability indices
        skill_indices = [capabilities.index(cap) for cap in skill_caps]

        # Verify ordering: MCP < Skills < ProcessHistory
        last_mcp_idx = max(mcp_indices)
        first_skill_idx = min(skill_indices)
        first_ph_idx = min(process_history_indices)

        assert last_mcp_idx < first_skill_idx, (
            f"MCP caps (max idx {last_mcp_idx}) should come before "
            f"SkillCaps (min idx {first_skill_idx})"
        )
        assert max(skill_indices) < first_ph_idx, (
            f"SkillCaps (max idx {max(skill_indices)}) should come before "
            f"ProcessHistory (first idx {first_ph_idx})"
        )


# ---------------------------------------------------------------------------
# Test: No agent_pool → no SkillCapability
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_skill_capability_not_included_without_agent_pool(
    mock_agent: Agent[Any],
    mock_mcp_manager: MagicMock,
) -> None:
    """No SkillCapability when agent_pool is not set."""
    mock_agent.mcp = mock_mcp_manager
    mock_agent.agent_pool = None

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []

        skill_caps = [cap for cap in capabilities if isinstance(cap, SkillCapability)]
        assert len(skill_caps) == 0, "No SkillCapability expected without agent_pool"


# ---------------------------------------------------------------------------
# Test: agent_pool with skills=None → no SkillCapability
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_skill_capability_not_included_without_skills_manager(
    mock_agent: Agent[Any],
    mock_mcp_manager: MagicMock,
) -> None:
    """No SkillCapability when agent_pool has no skill_capabilities."""
    mock_agent.mcp = mock_mcp_manager
    mock_pool = MagicMock()
    # skill_capabilities is empty list — no skills discovered
    mock_pool.skill_capabilities = []
    mock_ctx = MagicMock()
    mock_ctx.pool = mock_pool
    mock_pool.get_context.return_value = mock_ctx
    mock_agent.agent_pool = mock_pool

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []

        skill_caps = [cap for cap in capabilities if isinstance(cap, SkillCapability)]
        assert len(skill_caps) == 0, "No SkillCapability expected without skills manager"


# ---------------------------------------------------------------------------
# Test: Skills with disable_model_invocation=True → no SkillCapability
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_skill_capability_skipped_when_disabled(
    mock_agent: Agent[Any],
    mock_mcp_manager: MagicMock,
) -> None:
    """Skills with disable_model_invocation=True are filtered at pool level."""
    mock_agent.mcp = mock_mcp_manager
    mock_pool = MagicMock()
    # Pool-level filtering removes disabled skills, so skill_capabilities is empty
    mock_pool.skill_capabilities = []
    mock_ctx = MagicMock()
    mock_ctx.pool = mock_pool
    mock_pool.get_context.return_value = mock_ctx
    mock_agent.agent_pool = mock_pool

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []

        skill_caps = [cap for cap in capabilities if isinstance(cap, SkillCapability)]
        assert len(skill_caps) == 0, "No SkillCapability for disabled skill"


# ---------------------------------------------------------------------------
# Test: Skills not visible to node → no SkillCapability
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_skill_capability_skipped_when_not_visible(
    mock_agent: Agent[Any],
    mock_mcp_manager: MagicMock,
) -> None:
    """Skills not visible to node are skipped via is_skill_visible_to_node."""
    from agentpool.skills.capability import SkillCapability

    mock_agent.mcp = mock_mcp_manager
    mock_pool = MagicMock()

    invisible_skill = _make_mock_skill("invisible-skill")
    cap = SkillCapability(invisible_skill)
    mock_pool.skill_capabilities = [cap]

    # is_skill_visible_to_node returns False — skill is filtered out
    mock_pool.is_skill_visible_to_node = MagicMock(return_value=False)
    mock_ctx = MagicMock()
    mock_ctx.pool = mock_pool
    mock_pool.get_context.return_value = mock_ctx
    mock_agent.agent_pool = mock_pool

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []

        skill_caps = [cap for cap in capabilities if isinstance(cap, SkillCapability)]
        assert len(skill_caps) == 0, "No SkillCapability for filtered skill"
        mock_pool.is_skill_visible_to_node.assert_called_once_with(invisible_skill, mock_agent.name)


# ---------------------------------------------------------------------------
# Test: Mixture of visible and invisible skills only includes visible ones
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_skill_capability_mixed_visibility(
    mock_agent: Agent[Any],
    mock_mcp_manager: MagicMock,
) -> None:
    """Visible skills included, invisible ones excluded."""
    from agentpool.skills.capability import SkillCapability

    mock_agent.mcp = mock_mcp_manager
    mock_pool = MagicMock()

    visible_skill = _make_mock_skill("visible-skill")
    invisible_skill = _make_mock_skill("invisible-skill")
    cap_visible = SkillCapability(visible_skill)
    cap_invisible = SkillCapability(invisible_skill)
    mock_pool.skill_capabilities = [cap_visible, cap_invisible]

    # Only visible-skill passes the visibility check
    def visibility_check(skill: Any, node_name: str | None) -> bool:
        return skill.name == "visible-skill"

    mock_pool.is_skill_visible_to_node = visibility_check
    mock_ctx = MagicMock()
    mock_ctx.pool = mock_pool
    mock_pool.get_context.return_value = mock_ctx
    mock_agent.agent_pool = mock_pool

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        mock_pydantic_agent.return_value = MagicMock()
        await mock_agent.get_agentlet(None, None, None)

        call_kwargs = mock_pydantic_agent.call_args.kwargs
        capabilities = call_kwargs.get("capabilities", []) or []

        skill_caps = [cap for cap in capabilities if isinstance(cap, SkillCapability)]
        assert len(skill_caps) == 1, f"Expected 1 SkillCapability (visible), got {len(skill_caps)}"
