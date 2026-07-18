"""Shared fixtures and helpers for team-mode tests.

Provides:
- ``team_mode_pool`` / ``team_mode_pool_with_defaults`` fixtures (re-exported)
- Message inspection helpers (ported from pydantic-ai-harness patterns)
- ``build_agent_context`` helper for direct capability testing
- ``FunctionModel`` factory helpers for scripted multi-turn flows

See ``tests/AGENTS.md`` for the L1-L4 testing guide.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

# Re-export fixtures so tests in this directory can use them directly.
from tests.fixtures.team_mode_pool import (  # noqa: F401
    team_mode_pool,
    team_mode_pool_with_defaults,
)


if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage, ModelResponse, ToolReturnPart

    from agentpool import AgentPool
    from agentpool.capabilities.agent_context import AgentContext
    from agentpool_config.team_mode import TeamModeConfig


# ---------------------------------------------------------------------------
# Message inspection helpers (ported from pydantic-ai-harness patterns)
# ---------------------------------------------------------------------------


def _tool_returns_by_name(messages: list[ModelMessage], tool_name: str) -> list[ToolReturnPart]:
    """Extract ``ToolReturnPart`` entries matching ``tool_name`` from messages."""
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    return [
        part
        for msg in messages
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == tool_name
    ]


def _tool_call_names(messages: list[ModelMessage]) -> list[str]:
    """Extract ordered tool call names from ``ModelResponse`` entries."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    return [
        part.tool_name
        for msg in messages
        if isinstance(msg, ModelResponse)
        for part in msg.parts
        if isinstance(part, ToolCallPart)
    ]


def _user_prompt_text(messages: list[ModelMessage]) -> str:
    """Extract the user prompt text from the first ``ModelRequest``."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return ""


# ---------------------------------------------------------------------------
# AgentContext builder for direct capability testing
# ---------------------------------------------------------------------------


def build_agent_context(
    pool: AgentPool[Any],
    session_id: str,
    team_mode_config: TeamModeConfig,
) -> AgentContext:
    """Construct a real ``AgentContext`` for calling team tools directly.

    This mirrors what the RunLoop creates per-turn, allowing tests to
    invoke ``TeamCommCapability`` methods without going through
    ``Agent.run()``.
    """
    from agentpool.capabilities.agent_context import AgentContext
    from agentpool.capabilities.runloop_delegation import RunLoopDelegationService
    from agentpool.host.context import RunScope
    from agentpool.host.registry import AgentRegistry

    session_pool = pool.session_pool
    assert session_pool is not None
    session = session_pool.sessions.get_session(session_id)
    assert session is not None

    host_ctx = pool.get_context()
    registry = AgentRegistry(dict.fromkeys(pool.manifest.agents))
    delegation = RunLoopDelegationService(
        registry=registry,
        host=host_ctx,
        session_id=session_id,
    )
    scope = RunScope(
        config_id=None,
        tenant_id=None,
        user_id=None,
        session_id=session_id,
    )
    return AgentContext(
        agent_registry=registry,
        delegation=delegation,
        session=session,
        scope=scope,
        host=host_ctx,
        team_mode_config=team_mode_config,
    )


def make_mock_run_context(agent_ctx: AgentContext) -> MagicMock:
    """Create a mock pydantic-ai ``RunContext`` with ``AgentContext`` as deps.

    ``_resolve_agent_context`` checks ``isinstance(deps, AgentContext)``
    from ``capabilities.agent_context`` — our ``AgentContext`` matches that
    check and is returned directly.
    """
    ctx: Any = MagicMock()
    ctx.deps = agent_ctx
    return ctx


# ---------------------------------------------------------------------------
# FunctionModel factory helpers (ported from pydantic-ai-harness patterns)
# ---------------------------------------------------------------------------


def make_lifecycle_model(steps: list[tuple[str, dict[str, Any]]]) -> Any:
    """Create a ``FunctionModel`` that issues tool calls in sequence.

    Args:
        steps: Ordered list of ``(tool_name, args)`` tuples.  After all
            tool calls are issued, the model returns a final text response.

    Returns:
        A ``FunctionModel`` instance.
    """
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    calls: dict[str, int] = {"n": 0}

    def model_fn(messages: list[Any], info: Any) -> ModelResponse:
        calls["n"] += 1
        idx = calls["n"] - 1
        if idx < len(steps):
            tool_name, args = steps[idx]
            return ModelResponse(
                parts=[ToolCallPart(tool_name=tool_name, args=args, tool_call_id=f"call_{idx}")],
            )
        return ModelResponse(parts=[TextPart(content="done")])

    return FunctionModel(model_fn)
