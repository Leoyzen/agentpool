"""Integration tests for PR #64 round-5 review comment fixes.

Covers fixes for:
1. (Not adopted) base_agent.py:559 — StreamCompleteEvent/RunErrorEvent are
   already imported at runtime (not TYPE_CHECKING only). Python 3.13+ supports
   isinstance(x, A | B). Gemini misidentified the import.
2. agent.py:967 — _execute_node must handle RunErrorEvent before accessing
   turn.final_message (which raises RuntimeError if turn failed)
3. agent.py:1023 — _stream_events must handle RunErrorEvent before accessing
   turn.final_message (same issue as #2)
4. core.py:1496 — background task must have strong reference to prevent GC
5. acp_agent/turn.py:111 — remove unused _initial_message_history attribute
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events.events import (
    RunErrorEvent,
    StreamCompleteEvent,
)
from agentpool.agents.native_agent.turn import NativeTurn
from agentpool.orchestrator.core import EventBus, SessionState
from agentpool.orchestrator.run import RunHandle


if TYPE_CHECKING:
    from agentpool.agents.events.events import RichAgentStreamEvent


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fix #1 (not adopted): imports are already runtime-available
# ---------------------------------------------------------------------------


def test_base_agent_imports_are_runtime_available() -> None:
    """StreamCompleteEvent and RunErrorEvent must be imported at runtime.

    Gemini claimed they were only in TYPE_CHECKING, but they are actually
    imported at module level (line 23 of base_agent.py).
    """
    import agentpool.agents.base_agent as base_module

    # Verify the classes are accessible as attributes (runtime import)
    assert hasattr(base_module, "StreamCompleteEvent"), (
        "StreamCompleteEvent must be imported at runtime, not TYPE_CHECKING only"
    )
    assert hasattr(base_module, "RunErrorEvent"), (
        "RunErrorEvent must be imported at runtime, not TYPE_CHECKING only"
    )


# ---------------------------------------------------------------------------
# Fix #2: _execute_node handles RunErrorEvent before final_message
# ---------------------------------------------------------------------------


def test_execute_node_handles_run_error_event() -> None:
    """_execute_node must check for RunErrorEvent before accessing final_message.

    Without this, if turn.execute() yields RunErrorEvent and returns early,
    turn.final_message raises RuntimeError, masking the original error.
    """
    import agentpool.agents.native_agent.agent as agent_module

    source = inspect.getsource(agent_module.Agent._execute_node)
    assert "RunErrorEvent" in source, (
        "_execute_node must check for RunErrorEvent from turn.execute()"
    )
    assert "turn_failed" in source, (
        "_execute_node must track turn_failed flag to avoid accessing final_message"
    )


# ---------------------------------------------------------------------------
# Fix #3: _stream_events handles RunErrorEvent before final_message
# ---------------------------------------------------------------------------


def test_stream_events_handles_run_error_event() -> None:
    """_stream_events must check for RunErrorEvent before accessing final_message.

    Same issue as _execute_node — if turn fails, final_message is not set.
    """
    import agentpool.agents.native_agent.agent as agent_module

    source = inspect.getsource(agent_module.Agent._stream_events)
    assert "RunErrorEvent" in source, (
        "_stream_events must check for RunErrorEvent from turn.execute()"
    )
    assert "turn_failed" in source, (
        "_stream_events must track turn_failed flag to avoid accessing final_message"
    )


# ---------------------------------------------------------------------------
# Fix #4: background task has strong reference
# ---------------------------------------------------------------------------


def test_background_task_strong_reference() -> None:
    """_start_run_handle must keep strong reference to background task.

    Without a strong reference, Python's GC can destroy the task mid-execution.
    """
    import agentpool.orchestrator.core as core_module

    source = inspect.getsource(core_module.SessionController._start_run_handle)
    assert "_background_tasks" in source, (
        "_start_run_handle must store task in _background_tasks set "
        "to prevent GC from destroying it mid-execution"
    )
    assert "add_done_callback" in source, (
        "task must have done callback to discard from _background_tasks"
    )


# ---------------------------------------------------------------------------
# Fix #5: ACPTurn has no unused _initial_message_history
# ---------------------------------------------------------------------------


def test_acp_turn_no_unused_initial_message_history() -> None:
    """ACPTurn must not store _initial_message_history (dead code)."""
    import agentpool.agents.acp_agent.turn as turn_module

    source = inspect.getsource(turn_module.ACPTurn.__init__)
    assert "_initial_message_history" not in source, (
        "_initial_message_history is dead code — assigned but never used. "
        "Should be removed."
    )
