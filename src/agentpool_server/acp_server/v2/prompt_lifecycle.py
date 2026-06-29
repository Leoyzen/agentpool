"""ACP v2 prompt lifecycle state machine.

In v2, ``session/prompt`` returns immediately with an empty result.
The agent processes asynchronously and communicates state via
``state_update`` notifications: running → idle/requires_action.
"""

from __future__ import annotations

from typing import Literal

from agentpool.log import get_logger


logger = get_logger(__name__)

SessionState = Literal["idle", "running", "requires_action"]


class PromptLifecycleManager:
    """Track session state transitions for v2 prompt lifecycle.

    State transitions:
        idle → running  (prompt accepted)
        running → idle  (turn complete, with stopReason)
        running → requires_action  (needs user input)
        requires_action → running  (user responded)
    """

    def __init__(self) -> None:
        self._state: SessionState = "idle"
        self._stop_reason: str | None = None

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    def transition_to_running(self) -> SessionState:
        if self._state == "idle":
            self._state = "running"
            self._stop_reason = None
            logger.debug("State transition: idle → running")
        elif self._state == "requires_action":
            self._state = "running"
            self._stop_reason = None
            logger.debug("State transition: requires_action → running")
        return self._state

    def transition_to_idle(self, stop_reason: str = "end_turn") -> SessionState:
        self._state = "idle"
        self._stop_reason = stop_reason
        logger.debug("State transition: → idle", stop_reason=stop_reason)
        return self._state

    def transition_to_requires_action(self) -> SessionState:
        self._state = "requires_action"
        self._stop_reason = None
        logger.debug("State transition: → requires_action")
        return self._state
