"""Abstract base class for a single reactive cycle of agent execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from pydantic_ai.messages import ModelMessage

    from agentpool.agents.context import AgentRunContext
    from agentpool.agents.events.events import RichAgentStreamEvent
    from agentpool.hooks import AgentHooks
    from agentpool.hooks.base import HookResult
    from agentpool.messaging import ChatMessage


class Turn(ABC):
    """Abstract base class for a single reactive cycle of agent execution.

    A Turn encapsulates one complete reactive cycle: receiving input, executing
    through an agent (or agent team), and producing output events. Subclasses
    implement :meth:`execute` to drive the agent loop and yield stream events.

    After execution completes, :attr:`message_history` and :attr:`final_message`
    become available.
    """

    _message_history: list[ModelMessage] | None = None
    """Message history populated after execute() completes."""

    _final_message: ChatMessage[Any] | None = None
    """Final message populated after execute() completes."""

    @abstractmethod
    async def execute(self) -> AsyncGenerator[RichAgentStreamEvent[Any]]:
        """Execute one reactive cycle of agent interaction.

        Yields stream events during execution (text deltas, tool calls,
        lifecycle notifications) and populates ``_message_history`` and
        ``_final_message`` before returning.
        """
        ...  # pragma: no cover
        yield  # type: ignore[misc]  # pragma: no cover  # Makes this an async generator

    @property
    def message_history(self) -> list[ModelMessage]:
        """Return the message history after execute() completes.

        Returns:
            The list of model messages from the completed turn.

        Raises:
            RuntimeError: If accessed before :meth:`execute` completes.
        """
        if self._message_history is None:
            raise RuntimeError("message_history is not available until execute() completes")
        return self._message_history

    @property
    def final_message(self) -> ChatMessage[Any]:
        """Return the final chat message after execute() completes.

        Returns:
            The final :class:`ChatMessage` produced by the turn.

        Raises:
            RuntimeError: If accessed before :meth:`execute` completes.
        """
        if self._final_message is None:
            raise RuntimeError("final_message is not available until execute() completes")
        return self._final_message


class HookAwareTurn:
    """Mixin providing unified hook firing for both native and ACP turns.

    This mixin does **not** inherit from :class:`Turn`. Host classes use it via
    cooperative multiple inheritance::

        class NativeTurn(HookAwareTurn, Turn): ...
        class ACPTurn(HookAwareTurn, Turn): ...

    Host classes must:
    - Set ``self._hooks`` (an :class:`AgentHooks` or ``None``) in ``__init__``
      via a new ``hooks`` parameter.
    - Set ``self._run_ctx`` (an :class:`AgentRunContext`) in ``__init__``.
    - Implement the three abstract properties: :attr:`_hook_env`,
      :attr:`_hook_agent_name`, and :attr:`_hook_prompt`.

    All methods are no-ops when ``self._hooks`` is ``None``. The
    ``hooks_fired`` set on :attr:`_run_ctx` prevents double-firing when both
    the old (capability-based) and new (mixin-based) code paths are active
    during migration.
    """

    _hooks: AgentHooks | None
    """Hooks container, set by host class ``__init__``. ``None`` = no hooks."""

    _run_ctx: AgentRunContext
    """Per-run context, set by host class ``__init__``. Provides ``hooks_fired``."""

    @property
    @abstractmethod
    def _hook_env(self) -> Any | None:
        """Execution environment for command hooks.

        Host classes return their agent's :class:`ExecutionEnvironment` or
        ``None`` if not applicable (e.g., ACP agents without an env).
        """
        ...  # pragma: no cover

    @property
    @abstractmethod
    def _hook_agent_name(self) -> str:
        """Agent name passed to hook invocations."""
        ...  # pragma: no cover

    @property
    @abstractmethod
    def _hook_prompt(self) -> str:
        """The user prompt for this turn."""
        ...  # pragma: no cover

    async def _fire_pre_turn_hooks(self) -> HookResult | None:
        """Fire pre_turn hooks if not already fired this turn.

        Returns:
            Combined :class:`HookResult`, or ``None`` if hooks are not
            configured or already fired.
        """
        if self._hooks is None:
            return None
        if "pre_turn" in self._run_ctx.hooks_fired:
            return None
        self._run_ctx.hooks_fired.add("pre_turn")
        return await self._hooks.run_pre_turn_hooks(
            agent_name=self._hook_agent_name,
            prompt=self._hook_prompt,
            session_id=self._run_ctx.session_id,
            env=self._hook_env,
        )

    async def _fire_post_turn_hooks(
        self, result: ChatMessage[Any] | None, duration_ms: float = 0.0
    ) -> HookResult | None:
        """Fire post_turn hooks if not already fired this turn.

        Must be called in a ``finally`` block by host classes to ensure
        hooks fire even when the turn raises or is cancelled.

        Args:
            result: The final chat message from the turn, or ``None`` if
                the turn failed before producing one.
            duration_ms: Elapsed wall-clock time for this turn in
                milliseconds. Falls back to ``0.0`` when not provided.

        Returns:
            Combined :class:`HookResult`, or ``None`` if hooks are not
            configured or already fired.
        """
        if self._hooks is None:
            return None
        if "post_turn" in self._run_ctx.hooks_fired:
            return None
        self._run_ctx.hooks_fired.add("post_turn")
        return await self._hooks.run_post_turn_hooks(
            agent_name=self._hook_agent_name,
            prompt=self._hook_prompt,
            result=result,
            session_id=self._run_ctx.session_id,
            env=self._hook_env,
            duration_ms=duration_ms,
        )

    async def _fire_pre_tool_hooks(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_call_id: str | None = None,
    ) -> HookResult | None:
        """Fire pre_tool_use hooks for a tool call.

        Args:
            tool_name: Name of the tool being called.
            tool_input: Input arguments for the tool.
            tool_call_id: Unique ID for this tool call, if available.

        Returns:
            Combined :class:`HookResult`, or ``None`` if hooks are not
            configured or already fired for this tool call.
        """
        if self._hooks is None:
            return None
        if tool_call_id is not None:
            guard_key = f"pre_tool_use:{tool_call_id}"
        else:
            guard_key = f"pre_tool_use:{tool_name}"
        if guard_key in self._run_ctx.hooks_fired:
            return None
        self._run_ctx.hooks_fired.add(guard_key)
        return await self._hooks.run_pre_tool_hooks(
            agent_name=self._hook_agent_name,
            tool_name=tool_name,
            tool_input=tool_input,
            session_id=self._run_ctx.session_id,
            env=self._hook_env,
        )

    def _log_tool_execution(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: Any,
        tool_call_id: str | None = None,
    ) -> None:
        """Log a tool execution record to the journal for crash recovery.

        Creates a :class:`ToolExecutionRecord` and stores it via
        ``journal.log_tool_execution()``. Uses a separate guard key
        (``"tool_log:{tool_call_id}"``) to prevent double-logging when
        both old and new code paths are active.

        Skips silently if any of the following are missing:
        - ``run_ctx._run_handle`` (not running inside a pooled session)
        - ``run_handle._journal`` (no journal configured)
        - ``run_ctx.turn_id`` (turn_id not yet set)

        Args:
            tool_name: Name of the tool that was called.
            tool_input: Input arguments that were passed to the tool.
            tool_output: Output from the tool.
            tool_call_id: Unique ID for this tool call, if available.
        """
        from agentpool.lifecycle.types import ToolExecutionRecord

        # Separate guard to prevent double-logging.
        if tool_call_id is not None:
            log_guard = f"tool_log:{tool_call_id}"
        else:
            log_guard = f"tool_log:{tool_name}"
        if log_guard in self._run_ctx.hooks_fired:
            return
        self._run_ctx.hooks_fired.add(log_guard)

        run_handle = self._run_ctx._run_handle
        if run_handle is None:
            return
        journal = run_handle._journal
        if journal is None:
            return
        turn_id = self._run_ctx.turn_id
        if turn_id is None:
            return
        record = ToolExecutionRecord(
            turn_id=turn_id,
            tool_name=tool_name,
            args=tool_input,
            result=tool_output,
            status="completed",
        )
        journal.log_tool_execution(record)

    async def _fire_post_tool_hooks(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: Any,
        duration_ms: float,
        tool_call_id: str | None = None,
    ) -> HookResult | None:
        """Fire post_tool_use hooks for a completed tool call.

        Also logs the tool execution to the journal for crash recovery,
        independent of whether hooks are configured.

        Args:
            tool_name: Name of the tool that was called.
            tool_input: Input arguments that were passed to the tool.
            tool_output: Output from the tool.
            duration_ms: How long the tool took to execute in milliseconds.
            tool_call_id: Unique ID for this tool call, if available.

        Returns:
            Combined :class:`HookResult`, or ``None`` if hooks are not
            configured or already fired for this tool call.
        """
        # Log tool execution to journal (independent of hooks).
        self._log_tool_execution(tool_name, tool_input, tool_output, tool_call_id)

        if self._hooks is None:
            return None
        if tool_call_id is not None:
            guard_key = f"post_tool_use:{tool_call_id}"
        else:
            guard_key = f"post_tool_use:{tool_name}"
        if guard_key in self._run_ctx.hooks_fired:
            return None
        self._run_ctx.hooks_fired.add(guard_key)
        return await self._hooks.run_post_tool_hooks(
            agent_name=self._hook_agent_name,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            duration_ms=duration_ms,
            session_id=self._run_ctx.session_id,
            env=self._hook_env,
        )
