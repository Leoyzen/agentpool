"""Hook manager for NativeAgent.

Centralizes all hook-related logic:
- AgentHooks integration (pre/post run, pre/post tool)
- Injection consumption from PromptInjectionManager
- Combined hook result handling
- Unified tool interception via ``_ToolInterceptCapability``

Hook Migration Audit (Phase 6)
==============================

This module documents the migration status of legacy ``AgentHooks``
(from ``hooks/agent_hooks.py``) to pydantic-ai ``Capability`` hooks.

**pre_turn** -> ``Hooks.before_run``:
    Documented only. ``AgentHooks.as_capability()`` returns ``Hooks``
    with ``before_run`` registered. However,
    ``NativeAgentHookManager.as_capability()`` **strips** all ``Hooks``
    hooks (lines 429-433) to prevent double-firing with the old
    ``run_pre_turn_hooks()`` delegate. When ``ResourceProvider`` is
    fully removed (Phase 5 complete) and all callers migrate to
    Capabilities, ``before_run`` can be unstripped.

**post_turn** -> ``Hooks.after_run``:
    Same status as ``pre_turn``. Stripped in ``as_capability()``.
    Old ``run_post_turn_hooks()`` delegate remains for backward compat.

**pre_tool_use** -> ``before_tool_execute`` (on
``_ToolInterceptCapability``):
    Fully migrated. ``_ToolInterceptCapability.before_tool_execute()``
    (line 182) calls ``run_pre_tool_hooks()``. Legacy ``Hooks``
    ``before_tool_execute`` is STRIPPED (line 430) to prevent
    double-firing.

**post_tool_use** -> ``after_tool_execute`` (on
``_ToolInterceptCapability``):
    Fully migrated. ``_ToolInterceptCapability.after_tool_execute()``
    (line 239) calls ``run_post_tool_hooks()``. Legacy ``Hooks``
    ``after_tool_execute`` is STRIPPED (line 429) to prevent
    double-firing.

Hooks that remain distinct:

- ``wrap_node_run``: Not migrated; no equivalent in legacy
  ``AgentHooks``. New capability hook introduced in Phase 6.
- ``wrap_tool_execute``: Unified into
  ``_ToolInterceptCapability.wrap_tool_execute()`` (line 120).
  Handles error wrapping, timing, and ``ToolResult`` conversion.
  Does NOT duplicate the legacy pre/post hooks — it is a separate
  concern (error boundary).

Migration Plan:
1. When all callers of ``run_pre_turn_hooks()`` /
   ``run_post_turn_hooks()`` are migrated to Capabilities, remove the
   ``base_hooks._registry[...] = []`` stripping in
   ``as_capability()`` (lines 429-432).
2. Remove the legacy ``run_pre_turn_hooks()`` /
   ``run_post_turn_hooks()`` methods from this class.
3. Remove ``NativeAgentHookManager._agent`` and ``agent_hooks``
   parameters.
"""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING, Any
import warnings

from pydantic_ai.capabilities.abstract import AbstractCapability

from agentpool.hooks.base import HookResult
from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from exxec import ExecutionEnvironment
    from pydantic_ai.capabilities import CombinedCapability
    from pydantic_ai.capabilities.abstract import ValidatedToolArgs
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import RunContext, ToolDefinition
    from pydantic_ai.toolsets import AbstractToolset

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.hooks import AgentHooks

logger = get_logger(__name__)


@dataclass
class _ToolInterceptCapability(AbstractCapability[Any]):
    """Unified tool interception capability.

    Provides uniform tool interception across all tool sources (direct tools,
    MCP tools, ACP MCP tools) through pydantic-ai's ``AbstractCapability`` chain.

    Owns:
    - ``get_wrapper_toolset``: confirmation mode via ``ApprovalRequiredToolset``
    - ``prepare_tools``: schema modification (placeholder for future use)
    - ``wrap_tool_execute``: error handling with failure annotation
    - ``before_tool_execute``: pre-tool hooks + deny via ``ModelRetry``
    - ``after_tool_execute``: post-tool hooks + result modification + injection
    """

    _: KW_ONLY
    hook_manager: NativeAgentHookManager
    id: str | None = None
    description: str | None = None
    defer_loading: bool = False

    def get_wrapper_toolset(self, toolset: AbstractToolset[Any]) -> AbstractToolset[Any] | None:
        """Wrap the assembled toolset with ``ApprovalRequiredToolset`` based on mode.

        Reads ``tool_confirmation_mode`` from the agent's node config:
        - ``"always"``: all tools require approval
        - ``"never"``: no wrapper (tools execute directly)
        - ``"per_tool"``: only tools with ``requires_confirmation=True``

        Args:
            toolset: The agent's combined non-output toolset.

        Returns:
            A wrapped toolset or ``None`` if no wrapping is needed.
        """
        from pydantic_ai.toolsets import ApprovalRequiredToolset

        mode = self._get_confirmation_mode()

        if mode == "never":
            return None

        if mode == "always":
            return ApprovalRequiredToolset(
                wrapped=toolset,
                approval_required_func=lambda *_: True,
            )

        # mode == "per_tool": check each tool's requires_confirmation flag
        # ApprovalRequiredToolset expects a sync function, so we resolve
        # the confirmation set eagerly and check membership synchronously.
        confirm_tool_names = self._get_confirmation_tool_names()

        def _check_per_tool(
            ctx: RunContext[Any],
            tool_def: ToolDefinition,
            tool_args: dict[str, Any],
        ) -> bool:
            return tool_def.name in confirm_tool_names

        return ApprovalRequiredToolset(
            wrapped=toolset,
            approval_required_func=_check_per_tool,
        )

    async def prepare_tools(
        self,
        ctx: RunContext[Any],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        """Modify tool definitions before the model sees them.

        Currently a pass-through. Reserved for future schema modification
        (e.g., injecting bridge metadata into dynamic MCP tool descriptions).

        Args:
            ctx: The pydantic-ai run context.
            tool_defs: Current tool definitions.

        Returns:
            Tool definitions (unchanged for now).
        """
        return tool_defs

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> Any:
        """Wrap tool execution with error handling.

        Catches exceptions and returns annotated ``ToolReturn`` with failure
        details, enabling the model to recover or try alternatives.

        Args:
            ctx: The pydantic-ai run context.
            call: The tool call part.
            tool_def: The tool definition.
            args: Validated tool arguments.
            handler: The inner execution handler.

        Returns:
            Tool result, or annotated ``ToolReturn`` on failure.
        """
        from time import perf_counter

        from pydantic_ai.messages import ToolReturn

        from agentpool.tools.base import ToolResult

        agent_ctx = ctx.deps
        tool_start_times: dict[str, float] | None = getattr(agent_ctx, "_tool_start_times", None)
        if tool_start_times is None:
            tool_start_times = {}
            agent_ctx._tool_start_times = tool_start_times
        tool_start_times[call.tool_call_id] = perf_counter()

        try:
            result = await handler(args)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Tool execution failed",
                tool_name=call.tool_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return ToolReturn(
                return_value=f"Error: {exc}",
                content=f"Tool '{call.tool_name}' failed: {exc}",
            )

        # Convert AgentPool ToolResult to pydantic-ai ToolReturn
        if isinstance(result, ToolResult):
            val = result.structured_content or result.content
            result = ToolReturn(
                return_value=val,
                content=result.content,
                metadata=result.metadata,
            )

        return result

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
    ) -> ValidatedToolArgs:
        """Execute pre-tool hooks and handle deny.

        Runs pre-tool hooks from ``AgentHooks``. If a hook denies the tool
        call, raises ``ModelRetry`` to ask the model to try a different
        approach (instead of aborting the entire run).

        Args:
            ctx: The pydantic-ai run context.
            call: The tool call part.
            tool_def: The tool definition.
            args: Validated tool arguments.

        Returns:
            Possibly modified tool arguments.

        Raises:
            ModelRetry: If a pre-tool hook denies the tool call.
        """
        from pydantic_ai import ModelRetry

        from agentpool.agents.context import AgentContext

        agent_ctx = ctx.deps
        env = agent_ctx.agent.env if isinstance(agent_ctx, AgentContext) else None
        session_id = (
            agent_ctx.run_ctx.session_id
            if isinstance(agent_ctx, AgentContext) and agent_ctx.run_ctx
            else None
        )

        hook_result = await self.hook_manager.run_pre_tool_hooks(
            agent_name=self.hook_manager.agent_name,
            tool_name=call.tool_name,
            tool_input=dict(args),
            session_id=session_id,
            env=env,
            agent_context=agent_ctx,
        )

        if hook_result["decision"] == "deny":
            reason = hook_result.get("reason", "Blocked by pre-tool hook")
            raise ModelRetry(f"Tool '{call.tool_name}' blocked: {reason}")

        # Apply modified input if provided
        if modified := hook_result.get("modified_input"):
            return {**dict(args), **modified}

        return args

    async def after_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        result: Any,
    ) -> Any:
        """Execute post-tool hooks, apply modifications, and consume injections.

        Runs post-tool hooks from ``AgentHooks`` and explicitly applies:
        - ``modified_output``: replaces the tool result entirely
        - ``additional_context``: appended to the tool result

        Also consumes pending prompt injections from
        ``PromptInjectionManager``.

        This fixes the existing gap where ``AgentHooks._wrap_after_tool_execute``
        discards ``modified_output`` and ``additional_context``.

        Args:
            ctx: The pydantic-ai run context.
            call: The tool call part.
            tool_def: The tool definition.
            args: Validated tool arguments.
            result: The tool execution result.

        Returns:
            Possibly modified tool result.
        """
        from agentpool.agents.context import AgentContext
        from agentpool.agents.native_agent.tool_wrapping import (
            _inject_additional_context,
        )

        agent_ctx = ctx.deps
        env = agent_ctx.agent.env if isinstance(agent_ctx, AgentContext) else None
        session_id = (
            agent_ctx.run_ctx.session_id
            if isinstance(agent_ctx, AgentContext) and agent_ctx.run_ctx
            else None
        )

        from time import perf_counter

        tool_start_times: dict[str, float] | None = getattr(agent_ctx, "_tool_start_times", None)
        if tool_start_times is not None:
            start_time = tool_start_times.pop(call.tool_call_id, None)
            duration_ms = (perf_counter() - start_time) * 1000 if start_time else 0.0
        else:
            duration_ms = 0.0

        hook_result = await self.hook_manager.run_post_tool_hooks(
            agent_name=self.hook_manager.agent_name,
            tool_name=call.tool_name,
            tool_input=dict(args),
            tool_output=result,
            duration_ms=duration_ms,
            session_id=session_id,
            env=env,
            agent_context=agent_ctx,
        )

        # Apply modified_output (replaces result entirely)
        if "modified_output" in hook_result:
            result = hook_result["modified_output"]

        # Apply additional_context (appended to result)
        if additional := hook_result.get("additional_context"):
            result = _inject_additional_context(result, additional)

        # Consume pending injection from PromptInjectionManager
        run_ctx = self.hook_manager._agent.get_active_run_context()
        injection_manager = run_ctx.injection_manager if run_ctx else None
        if injection_manager:
            injection = await injection_manager.consume()
            if injection:
                logger.debug(
                    "Consuming injection after tool use",
                    agent=self.hook_manager.agent_name,
                    tool=call.tool_name,
                    injection_len=len(injection),
                )
                result = _inject_additional_context(result, injection)

        return result

    def _get_confirmation_mode(self) -> str:
        """Read tool_confirmation_mode from the agent's node config.

        Returns:
            One of "always", "never", or "per_tool".
        """
        run_ctx = self.hook_manager._agent.get_active_run_context()
        if run_ctx is not None:
            node = run_ctx.node if hasattr(run_ctx, "node") else None
            if node is not None:
                return str(node.tool_confirmation_mode)
        agent = self.hook_manager._agent
        if hasattr(agent, "tool_confirmation_mode"):
            return str(agent.tool_confirmation_mode)
        return "per_tool"

    def _get_confirmation_tool_names(self) -> set[str]:
        """Get the set of tool names that require confirmation.

        Returns:
            Set of tool names with ``requires_confirmation=True``.
        """
        tool_manager = self.hook_manager._agent.tools
        try:
            # Access cached tools if available (get_tools is async, but
            # the tool list is typically populated during agent setup)
            tools = tool_manager._tools if hasattr(tool_manager, "_tools") else []
        except Exception:  # noqa: BLE001
            tools = []
        return {t.name for t in tools if t.requires_confirmation}


class NativeAgentHookManager:
    """Manages hooks and injection for NativeAgent.

    Responsibilities:
    - Wraps AgentHooks and delegates to it
    - Consumes injections from PromptInjectionManager (via agent's run context)
    - Combines injection with post-tool hook results
    - Provides unified tool interception via ``_ToolInterceptCapability``

    Example:
        hook_manager = NativeAgentHookManager(
            agent=agent,
            agent_hooks=hooks,
        )

        # Injections are queued via agent.inject_prompt()
        # Hook manager consumes them in post-tool hooks
        result = await hook_manager.run_post_tool_hooks(...)
        # result["additional_context"] contains the injection
    """

    def __init__(
        self,
        *,
        agent: BaseAgent[Any, Any],
        agent_hooks: AgentHooks | None = None,
    ) -> None:
        """Initialize hook manager.

        Args:
            agent: The agent instance (for accessing per-run injection manager)
            agent_hooks: Optional AgentHooks for pre/post hooks
        """
        self.agent_name = agent.name
        self.agent_hooks = agent_hooks
        self._agent = agent

    def has_hooks(self) -> bool:
        """Check if any hooks are configured."""
        return bool(self.agent_hooks and self.agent_hooks.has_hooks())

    def as_capability(self) -> CombinedCapability:
        """Return a ``CombinedCapability`` with unified tool interception.

        Returns a ``CombinedCapability`` containing:
        1. ``_ToolInterceptCapability`` — owns all tool interception (confirmation,
           hooks, injection, error handling)
        2. ``hooks_cap`` — preserved for ``before_run``/``after_run`` lifecycle
           callbacks, but its ``after_tool_execute`` is stripped to prevent
           double-firing (per Decision 2).

        The order ``[_ToolInterceptCapability(), hooks_cap]`` is correct:
        ``CombinedCapability`` chains ``after_tool_execute`` in reverse, so
        pass-through ``hooks_cap`` runs outermost first, then
        ``_ToolInterceptCapability`` runs innermost (runs hooks, applies
        results, consumes injections).

        Returns:
            A pydantic-ai ``CombinedCapability`` instance.
        """
        from pydantic_ai.capabilities import CombinedCapability, Hooks

        # Start with AgentHooks capability if available
        if self.agent_hooks and self.agent_hooks.has_hooks():
            base_hooks = self.agent_hooks.as_capability()
        else:
            base_hooks = Hooks()

        # _ToolInterceptCapability owns all tool interception (Decision 2).
        # base_agent.py owns before_run/after_run via the old mechanism.
        base_hooks._registry["after_tool_execute"] = []
        base_hooks._registry["before_tool_execute"] = []
        base_hooks._registry["before_run"] = []
        base_hooks._registry["after_run"] = []

        # Build the combined capability: _ToolInterceptCapability innermost,
        # hooks_cap outermost (for before_run/after_run lifecycle only).
        return CombinedCapability(
            capabilities=[
                _ToolInterceptCapability(hook_manager=self),
                base_hooks,
            ]
        )

    async def run_pre_turn_hooks(
        self,
        *,
        agent_name: str,
        prompt: str,
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Execute pre-turn hooks.

        Args:
            agent_name: Name of the agent.
            prompt: The prompt being processed.
            session_id: Optional conversation identifier.
            env: Agent's execution environment, passed to command hooks.

        Returns:
            Hook result. If decision is "deny", the run should be blocked.
        """
        if self.agent_hooks:
            return await self.agent_hooks.run_pre_turn_hooks(
                agent_name=agent_name,
                prompt=prompt,
                session_id=session_id,
                env=env,
            )
        return HookResult(decision="allow")

    async def run_post_turn_hooks(
        self,
        *,
        agent_name: str,
        prompt: str,
        result: Any,
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
        duration_ms: float = 0.0,
    ) -> HookResult:
        """Execute post-turn hooks.

        Args:
            agent_name: Name of the agent.
            prompt: The prompt that was processed.
            result: The result from the run.
            session_id: Optional conversation identifier.
            env: Agent's execution environment, passed to command hooks.
            duration_ms: How long the turn took to execute in milliseconds.

        Returns:
            Hook result.
        """
        if self.agent_hooks:
            return await self.agent_hooks.run_post_turn_hooks(
                agent_name=agent_name,
                prompt=prompt,
                result=result,
                session_id=session_id,
                env=env,
                duration_ms=duration_ms,
            )
        return HookResult(decision="allow")

    async def run_pre_run_hooks(
        self,
        *,
        agent_name: str,
        prompt: str,
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Deprecated alias for :meth:`run_pre_turn_hooks`.

        .. deprecated::
            Use :meth:`run_pre_turn_hooks` instead.
        """
        warnings.warn(
            "run_pre_run_hooks() is deprecated, use run_pre_turn_hooks() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return await self.run_pre_turn_hooks(
            agent_name=agent_name,
            prompt=prompt,
            session_id=session_id,
            env=env,
        )

    async def run_post_run_hooks(
        self,
        *,
        agent_name: str,
        prompt: str,
        result: Any,
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Deprecated alias for :meth:`run_post_turn_hooks`.

        .. deprecated::
            Use :meth:`run_post_turn_hooks` instead.
        """
        warnings.warn(
            "run_post_run_hooks() is deprecated, use run_post_turn_hooks() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return await self.run_post_turn_hooks(
            agent_name=agent_name,
            prompt=prompt,
            result=result,
            session_id=session_id,
            env=env,
        )

    async def run_pre_tool_hooks(
        self,
        *,
        agent_name: str,
        tool_name: str,
        tool_input: dict[str, Any],
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
        agent_context: Any | None = None,
    ) -> HookResult:
        """Execute pre-tool-use hooks.

        Args:
            agent_name: Name of the agent.
            tool_name: Name of the tool being called.
            tool_input: Input arguments for the tool.
            session_id: Optional conversation identifier.
            env: Agent's execution environment, passed to command hooks.
            agent_context: Optional AgentContext for hooks that need pool access.

        Returns:
            Hook result. If decision is "deny", the tool call should be blocked.
            May include modified_input to change tool arguments.
        """
        if self.agent_hooks:
            return await self.agent_hooks.run_pre_tool_hooks(
                agent_name=agent_name,
                tool_name=tool_name,
                tool_input=tool_input,
                session_id=session_id,
                env=env,
                agent_context=agent_context,
            )
        return HookResult(decision="allow")

    async def run_post_tool_hooks(
        self,
        *,
        agent_name: str,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: Any,
        duration_ms: float,
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
        agent_context: Any | None = None,
    ) -> HookResult:
        """Execute post-tool-use hooks and consume pending injection.

        This method combines:
        - Results from AgentHooks.run_post_tool_hooks()
        - Pending injection from PromptInjectionManager (if any)

        The injection is consumed after being included in the result.

        Args:
            agent_name: Name of the agent.
            tool_name: Name of the tool that was called.
            tool_input: Input arguments that were passed to the tool.
            tool_output: Output from the tool.
            duration_ms: How long the tool took.
            session_id: Optional conversation identifier.
            env: Agent's execution environment, passed to command hooks.
            agent_context: Optional AgentContext for hooks that need pool access.

        Returns:
            Combined hook result. May include additional_context from hooks
            and/or pending injection.
        """
        # Get result from AgentHooks
        if self.agent_hooks:
            result = await self.agent_hooks.run_post_tool_hooks(
                agent_name=agent_name,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_output=tool_output,
                duration_ms=duration_ms,
                session_id=session_id,
                env=env,
                agent_context=agent_context,
            )
        else:
            result = HookResult(decision="allow")

        # Consume pending injection from run context (isolated per-call)
        # Use get_active_run_context() for ContextVar + SessionPool fallback.
        run_ctx = self._agent.get_active_run_context()
        injection_manager = run_ctx.injection_manager if run_ctx else None
        if injection_manager:
            injection = await injection_manager.consume()
            if injection:
                logger.debug(
                    "Consuming injection after tool use",
                    agent=self.agent_name,
                    tool=tool_name,
                    injection_len=len(injection),
                )

                # Combine with existing additional_context
                existing_context = result.get("additional_context")
                if existing_context:
                    result["additional_context"] = f"{existing_context}\n\n{injection}"
                else:
                    result["additional_context"] = injection

        return result
