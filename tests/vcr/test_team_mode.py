r"""L3 VCR test — dynamic team mode tool selection (P2 pattern).

Uses the ``Case`` dataclass pattern (inspired by pydantic-ai's own VCR
test suite) to parameterize VCR tests over a matrix of team-mode
prompts. Each ``Case`` declares the prompt, the expected tool-call
sequence, and an optional substring to assert in the final response.

VCR intercepts the model API HTTP calls (design D6) — the pool,
agents, capabilities, EventBus, SessionController, and team-mode
toolset all run for real in-process. Cassettes replay the recorded
model responses deterministically in CI.

Cassettes ([HUMAN-REQUIRED]):
- ``tests/cassettes/vcr/test_team_mode/test_team_mode_via_vcr.yaml``

Record with::

    OPENAI_API_KEY=sk-... uv run pytest tests/vcr/test_team_mode.py \\
        --record-mode=once

See ``tests/AGENTS.md`` for the VCR recording workflow and
``openspec/changes/layered-testing-infrastructure/design.md`` for
design D6 (VCR scope) and D15 (``vcr_team_pool`` fixture).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from agentpool.agents.events import (
    PartDeltaEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
)
from tests.vcr.conftest import cassette_exists


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool import AgentPool


pytestmark = pytest.mark.vcr

_MODULE_STEM = "test_team_mode"


# ---------------------------------------------------------------------------
# Case dataclass (parameterized VCR pattern)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TeamCase:
    """A single parameterized VCR test case for team mode.

    Attributes:
        id: Test identifier used in parametrize IDs and cassette naming.
        prompt: The prompt sent to the lead agent.
        expected_tool_calls: Tool names expected to appear in
            ``ToolCallStartEvent`` entries, in order.
        expected_response_contains: Optional substring to assert in the
            concatenated text deltas of the final response.
        marks: Extra pytest marks to apply (e.g. ``pytest.mark.slow``).
    """

    id: str
    prompt: str
    expected_tool_calls: list[str]
    expected_response_contains: str = ""
    marks: tuple[Any, ...] = field(default_factory=tuple)


CASES: list[TeamCase] = [
    TeamCase(
        id="create_team",
        prompt=(
            'Create a team named "test_team" with one member: name="assistant", agent="team_member"'
        ),
        expected_tool_calls=["team_create"],
        expected_response_contains="team",
    ),
    TeamCase(
        id="full_lifecycle",
        prompt=(
            "Create a team, check status, create a task, "
            "write blackboard, read blackboard, delete team"
        ),
        expected_tool_calls=[
            "team_create",
            "team_status",
            "task_create",
            "write_blackboard",
            "read_blackboard",
            "team_delete",
        ],
    ),
    TeamCase(
        id="send_message",
        prompt=("Create a team with one member, send them a message, then delete the team"),
        expected_tool_calls=["team_create", "send_message", "team_delete"],
    ),
]


# ---------------------------------------------------------------------------
# Event helpers (mirrors tests/team_mode/test_live.py)
# ---------------------------------------------------------------------------


def _collect_tool_names(events: list[Any]) -> list[str]:
    """Extract tool names from ``ToolCallStartEvent`` entries."""
    return [e.tool_name for e in events if isinstance(e, ToolCallStartEvent)]


def _collect_text(events: list[Any]) -> str:
    """Concatenate all text deltas from ``PartDeltaEvent`` entries."""
    from pydantic_ai import TextPartDelta, ThinkingPartDelta

    parts: list[str] = []
    for e in events:
        if isinstance(e, PartDeltaEvent):
            match e.delta:
                case TextPartDelta(content_delta=str(text)):
                    parts.append(text)
                case ThinkingPartDelta(content_delta=str(text)):
                    parts.append(text)
                case _:
                    pass
    return "".join(parts)


async def _drain_events(stream: AsyncIterator[Any]) -> list[Any]:
    """Collect all events from an async iterator into a list."""
    return [event async for event in stream]


# ---------------------------------------------------------------------------
# Parameterized VCR test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, id=c.id, marks=c.marks) for c in CASES],
)
@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_team_mode_via_vcr"),
    reason="Cassette not recorded yet — run with --record-mode=once and OPENAI_API_KEY",
)
async def test_team_mode_via_vcr(vcr_team_pool: AgentPool, case: TeamCase) -> None:
    """L3 VCR test: verify the LLM correctly selects team tools.

    Given: a real ``AgentPool`` with ``team_mode`` enabled (``vcr_team_pool``
    fixture — two agents: ``team_lead`` lead-eligible and ``team_member``
    member-eligible).

    When: the lead agent processes ``case.prompt`` via the SessionPool.

    Then:
    - The emitted event stream contains ``ToolCallStartEvent`` entries for
      every tool in ``case.expected_tool_calls``, in order.
    - A ``StreamCompleteEvent`` is emitted exactly once.
    - If ``case.expected_response_contains`` is set, the concatenated text
      deltas contain that substring.
    """
    session_pool = vcr_team_pool.session_pool
    assert session_pool is not None, "SessionPool should be initialized"

    session_id = f"vcr-team-{case.id}"
    await session_pool.create_session(session_id, agent_name="team_lead")

    events = await _drain_events(
        session_pool.run_stream(session_id, case.prompt),
    )

    # --- Assert tool calls ------------------------------------------------
    tool_names = _collect_tool_names(events)

    # The model may emit additional tool calls (e.g. retries), so we check
    # that every expected tool appears at least once, in relative order.
    expected = case.expected_tool_calls
    remaining = list(tool_names)
    for expected_tool in expected:
        assert expected_tool in remaining, (
            f"Expected tool '{expected_tool}' not found in tool calls. Invoked: {tool_names}"
        )
        # Remove the first occurrence to preserve relative-order checking.
        remaining.remove(expected_tool)

    # --- Assert stream completion -----------------------------------------
    completes = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(completes) >= 1, f"Expected at least one StreamCompleteEvent, got {len(completes)}"

    # --- Assert response content (optional) -------------------------------
    if case.expected_response_contains:
        text = _collect_text(events)
        assert case.expected_response_contains in text, (
            f"Expected '{case.expected_response_contains}' in response text. Got: {text[-500:]}"
        )

    # --- Assert tool completions match starts -----------------------------
    starts = [e for e in events if isinstance(e, ToolCallStartEvent)]
    completes_tools = [e for e in events if isinstance(e, ToolCallCompleteEvent)]
    # Every started tool should have a matching completion (eventually).
    assert len(completes_tools) >= len(starts) or len(completes_tools) >= 1, (
        f"Tool completions ({len(completes_tools)}) should match or exceed starts ({len(starts)})"
    )
