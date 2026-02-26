"""Hook manager for ClaudeCodeAgent.

Centralizes all hook-related logic:
- Built-in hooks (injection via PostToolUse)
- AgentHooks integration
- Injection consumption from PromptInjectionManager
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from clawd_code_sdk.models import HookContext, HookInput, HookMatcher, SyncHookJSONOutput

    from agentpool.agents.prompt_injection import PromptInjectionManager
    from agentpool.hooks import AgentHooks

logger = get_logger(__name__)


class ClaudeCodeHookManager:
    """Manages SDK hooks for ClaudeCodeAgent.

    Responsibilities:
    - Builds SDK hooks configuration from multiple sources
    - Consumes injections from PromptInjectionManager
    - Provides clean API for hook-related operations
    """

    def __init__(
        self,
        *,
        agent_name: str,
        agent_hooks: AgentHooks | None = None,
        injection_manager: PromptInjectionManager | None = None,
        set_mode: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize hook manager.

        Args:
            agent_name: Name of the agent (for logging/events)
            agent_hooks: Optional AgentHooks for pre/post tool hooks
            injection_manager: Shared injection manager from BaseAgent
            set_mode: Callback to set agent mode (mode_id, category_id)
        """
        self.agent_name = agent_name
        self.agent_hooks = agent_hooks
        self._injection_manager = injection_manager
        self._set_mode = set_mode

    def build_hooks(self) -> dict[str, list[HookMatcher]]:
        """Build complete SDK hooks configuration.

        Combines:
        - Built-in hooks (injection via PostToolUse)
        - AgentHooks (pre/post tool use)

        Returns:
            Dictionary mapping hook event names to HookMatcher lists
        """
        from clawd_code_sdk.models import HookMatcher

        from agentpool.agents.claude_code_agent.converters import build_sdk_hooks_from_agent_hooks

        result: dict[str, list[Any]] = {}
        # Add PostToolUse hook for injection
        result["PostToolUse"] = [HookMatcher(matcher="*", hooks=[self._on_post_tool_use])]
        # Merge AgentHooks if present
        if self.agent_hooks:
            agent_hooks = build_sdk_hooks_from_agent_hooks(self.agent_hooks, self.agent_name)
            for event_name, matchers in agent_hooks.items():
                if event_name in result:
                    result[event_name].extend(matchers)
                else:
                    result[event_name] = matchers

        return result

    async def _on_post_tool_use(
        self,
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput:
        """Handle PostToolUse hook for injection and observation.

        Consumes pending injection from the shared PromptInjectionManager
        and adds it as additionalContext in the response.
        """
        from clawd_code_sdk.models import PostToolUseHookSpecificOutput

        result: SyncHookJSONOutput = {"continue_": True}
        # Consume pending injection from shared manager
        if self._injection_manager and (injection := await self._injection_manager.consume()):
            tool_name = input_data.get("tool_name", "unknown")
            logger.debug("Injecting context after tool use", agent=self.agent_name, tool=tool_name)
            result["hookSpecificOutput"] = PostToolUseHookSpecificOutput(
                hookEventName="PostToolUse",
                additionalContext=injection,
            )
        if input_data.get("tool_name") == "EnterPlanMode" and self._set_mode:
            await self._set_mode("plan", "mode")

        return result
