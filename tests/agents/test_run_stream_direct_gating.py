"""Tests for AGENT_TYPE gating in BaseAgent._execute_direct().

Verifies that the `if self.AGENT_TYPE == "native"` gating at
base_agent.py:1051 correctly skips the manual `while has_queued()` loop
for native agents and executes it for non-native agents.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, ClassVar

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RichAgentStreamEvent
from acp.schema import AvailableCommandsUpdate


# ---------------------------------------------------------------------------
# Test helper: minimal BaseAgent subclass that spies on _run_stream_once
# ---------------------------------------------------------------------------

class _FakeEvent:
    """Minimal concrete event for stream testing (no real event fields needed)."""


class _GatingTestAgent(BaseAgent[None, str]):
    """Test agent that tracks _run_stream_once calls.

    Subclasses override AGENT_TYPE to test native vs non-native gating.
    _run_stream_once records every call and queues an extra prompt on the first
    invocation, allowing the test to distinguish single-call (native) from
    multi-call (non-native) behaviour.
    """

    AGENT_TYPE: ClassVar[str]  # set by subclasses

    def __init__(self, call_log: list[tuple[Any, ...]]) -> None:
        super().__init__(name="gating_test")
        self._call_log = call_log
        self._has_queued_extra = False

    # -- concrete abstract methods -------------------------------------------

    @property
    def model_name(self) -> str | None:
        return "test-model"

    async def set_model(self, model: str) -> None:
        pass

    async def _stream_events(
        self,
        run_ctx: AgentRunContext,
        prompts: list[Any],
        *,
        user_msg: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[RichAgentStreamEvent[str]]:
        # Not called because _run_stream_once is overridden below.
        return
        yield  # pragma: no cover (make generator)

    async def _interrupt(self, run_ctx: AgentRunContext | None = None) -> None:
        pass

    async def get_available_models(self) -> None:
        return None

    async def get_modes(self) -> list[Any]:
        return []

    async def _set_mode(self, mode_id: str, category_id: str) -> None:
        pass

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        return []

    async def load_session(self, session_id: str) -> Any:
        return None

    # -- spied method --------------------------------------------------------

    async def _run_stream_once(
        self,
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RichAgentStreamEvent[str]]:
        self._call_log.append(prompts)
        # On the very first call, queue an extra prompt so the manual loop
        # (non-native) gets a second iteration while the native path does not.
        if not self._has_queued_extra:
            self._has_queued_extra = True
            run_ctx.injection_manager.queue("extra_prompt")
        yield _FakeEvent()  # type: ignore[return-value]


class _NativeTestAgent(_GatingTestAgent):
    """Agent with AGENT_TYPE = 'native' (skips manual loop)."""
    AGENT_TYPE: ClassVar = "native"


class _NonNativeTestAgent(_GatingTestAgent):
    """Agent with AGENT_TYPE = 'acp' (executes manual loop)."""
    AGENT_TYPE: ClassVar = "acp"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_native_agent_skips_manual_loop() -> None:
    """Native AGENT_TYPE should cause _execute_direct to skip the while loop.

    When AGENT_TYPE == 'native', the extra prompt queued during
    _run_stream_once must NOT be processed — the method uses a simple
    ``async for`` and exits without re-checking the injection queue.
    """
    call_log: list[tuple[Any, ...]] = []
    agent = _NativeTestAgent(call_log)

    events: list[object] = []
    async for event in agent._execute_direct("test prompt"):
        events.append(event)

    # Native path: _run_stream_once is called exactly once
    assert len(call_log) == 1, (
        f"Expected 1 call to _run_stream_once for native agent, "
        f"got {len(call_log)}"
    )
    # The queued extra prompt should still be in the injection manager
    assert agent._has_queued_extra, "Extra prompt should have been queued"
    # Sanity: we got the fake event
    assert len(events) == 1
    assert isinstance(events[0], _FakeEvent)


async def test_non_native_agent_executes_manual_loop() -> None:
    """Non-native AGENT_TYPE should cause _execute_direct to run the while loop.

    When AGENT_TYPE == 'acp', the extra prompt queued during
    _run_stream_once MUST be processed because the while loop re-checks
    ``has_queued()`` after each iteration.
    """
    call_log: list[tuple[Any, ...]] = []
    agent = _NonNativeTestAgent(call_log)

    events: list[object] = []
    async for event in agent._execute_direct("test prompt"):
        events.append(event)

    # Non-native path: _run_stream_once is called twice
    # (initial prompt + queued extra prompt)
    assert len(call_log) == 2, (
        f"Expected 2 calls to _run_stream_once for non-native agent, "
        f"got {len(call_log)}"
    )
    # First call should have the original prompt
    assert "test prompt" in call_log[0], (
        f"First call should contain original prompt, got {call_log[0]}"
    )
    # Second call should have the extra queued prompt
    assert "extra_prompt" in call_log[1], (
        f"Second call should contain queued extra prompt, got {call_log[1]}"
    )
    # Sanity: we got two fake events (one per _run_stream_once call)
    assert len(events) == 2
    assert all(isinstance(e, _FakeEvent) for e in events)
