"""Hook manager for NativeAgent.

Centralizes all hook-related logic:
- AgentHooks integration (pre/post run, pre/post tool)
- Injection consumption from PromptInjectionManager
- Combined hook result handling
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.hooks.base import HookResult
from agentpool.log import get_logger


if TYPE_CHECKING:
    from exxec import ExecutionEnvironment

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.hooks import AgentHooks

logger = get_logger(__name__)


class NativeAgentHookManager:
    """Manages hooks and injection for NativeAgent.

    Responsibilities:
    - Wraps AgentHooks and delegates to it
    - Consumes injections from PromptInjectionManager (via agent's run context)
    - Combines injection with post-tool hook results

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

    def as_capability(self) -> Any:
        """Return a pydantic-ai Hooks capability with injection consumption.

        Delegates to :meth:`AgentHooks.as_capability` for base hook behaviour
        and wraps ``after_tool_execute`` to consume pending prompt injections
        after each tool call.

        Returns:
            A pydantic-ai :class:`~pydantic_ai.capabilities.Hooks` instance.
        """
        from pydantic_ai.capabilities import Hooks
        from pydantic_ai.messages import ToolCallPart
        from pydantic_ai.tools import RunContext, ToolDefinition

        if TYPE_CHECKING:
            from pydantic_ai.capabilities.abstract import ValidatedToolArgs

        # Start with AgentHooks capability if available
        if self.agent_hooks and self.agent_hooks.has_hooks():
            base_hooks = self.agent_hooks.as_capability()
        else:
            base_hooks = Hooks()

        original_after_tool = base_hooks.after_tool_execute

        async def wrapped_after_tool(
            ctx: RunContext[Any],
            *,
            call: ToolCallPart,
            tool_def: ToolDefinition,
            args: ValidatedToolArgs,
            result: Any,
        ) -> Any:
            # Run original hook first if it exists
            if original_after_tool is not None:
                result = await original_after_tool(
                    ctx, call=call, tool_def=tool_def, args=args, result=result
                )

            # Consume pending injection from run context
            run_ctx = self._agent.get_active_run_context()
            injection_manager = run_ctx.injection_manager if run_ctx else None
            if injection_manager:
                injection = await injection_manager.consume()
                if injection:
                    from agentpool.agents.native_agent.tool_wrapping import (
                        _inject_additional_context,
                    )

                    result = _inject_additional_context(result, injection)

            return result

        # Build kwargs for new Hooks, preserving existing callbacks
        kwargs: dict[str, Any] = {"after_tool_execute": wrapped_after_tool}
        if self.agent_hooks and self.agent_hooks.has_hooks():
            if self.agent_hooks.pre_run:
                kwargs["before_run"] = base_hooks.before_run
            if self.agent_hooks.post_run:
                kwargs["after_run"] = base_hooks.after_run
            if self.agent_hooks.pre_tool_use:
                kwargs["before_tool_execute"] = base_hooks.before_tool_execute
            kwargs["ordering"] = base_hooks.get_ordering()

        new_hooks = Hooks(**kwargs)

        # Copy any additional registry entries from base hooks
        for key, entries in base_hooks._registry.items():
            if key not in {"before_run", "after_run", "before_tool_execute", "after_tool_execute"}:
                new_hooks._registry.setdefault(key, []).extend(entries)

        return new_hooks

    async def run_pre_run_hooks(
        self,
        *,
        agent_name: str,
        prompt: str,
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Execute pre-run hooks.

        Args:
            agent_name: Name of the agent.
            prompt: The prompt being processed.
            session_id: Optional conversation identifier.
            env: Agent's execution environment, passed to command hooks.

        Returns:
            Hook result. If decision is "deny", the run should be blocked.
        """
        if self.agent_hooks:
            return await self.agent_hooks.run_pre_run_hooks(
                agent_name=agent_name,
                prompt=prompt,
                session_id=session_id,
                env=env,
            )
        return HookResult(decision="allow")

    async def run_post_run_hooks(
        self,
        *,
        agent_name: str,
        prompt: str,
        result: Any,
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Execute post-run hooks.

        Args:
            agent_name: Name of the agent.
            prompt: The prompt that was processed.
            result: The result from the run.
            session_id: Optional conversation identifier.
            env: Agent's execution environment, passed to command hooks.

        Returns:
            Hook result.
        """
        if self.agent_hooks:
            return await self.agent_hooks.run_post_run_hooks(
                agent_name=agent_name,
                prompt=prompt,
                result=result,
                session_id=session_id,
                env=env,
            )
        return HookResult(decision="allow")

    async def run_pre_tool_hooks(
        self,
        *,
        agent_name: str,
        tool_name: str,
        tool_input: dict[str, Any],
        session_id: str | None = None,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        """Execute pre-tool-use hooks.

        Args:
            agent_name: Name of the agent.
            tool_name: Name of the tool being called.
            tool_input: Input arguments for the tool.
            session_id: Optional conversation identifier.
            env: Agent's execution environment, passed to command hooks.

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
            duration_ms: How long the tool took to execute.
            session_id: Optional conversation identifier.
            env: Agent's execution environment, passed to command hooks.

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
