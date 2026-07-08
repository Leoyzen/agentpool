"""HookProxy — wraps existing AgentHooks instances as a Proxy in the ACP chain.

Implements the Proxy protocol to intercept ACP messages and route them
through the agent's hook system. Maps all 4 hook types to ACP message flows.

Hook type mappings:
- session/prompt (request) → pre_turn (deny blocks, additional_context injected)
- session/update ToolCallStart → pre_tool_use (deny blocks, modified_input)
- session/update ToolCallComplete → post_tool_use (modified_output)
- session/prompt (response) → post_turn (correlated by request ID, NOT on chunks)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from agentpool.hooks.agent_hooks import AgentHooks

logger = logging.getLogger(__name__)


class HookProxy:
    """Proxy that wraps AgentHooks instances and routes ACP messages through hooks.

    Implements the Proxy protocol defined in acp.proxy.protocol.
    """

    def __init__(self, hooks: list[AgentHooks]) -> None:
        """Initialize the HookProxy with a list of AgentHooks.

        Args:
            hooks: List of AgentHooks instances to wrap.
        """
        self._hooks = hooks

    def proxy_initialize(self) -> list[str]:
        """Return the list of ACP methods this proxy intercepts.

        Returns:
            List of intercepted method names.
        """
        return ["session/prompt", "session/update"]

    async def proxy_successor(
        self,
        method: str,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Route an ACP message through the appropriate hook handler.

        Args:
            method: The ACP method name (e.g., "session/prompt").
            params: The method parameters.
            meta: Metadata about the message (may contain "response", "direction").

        Returns:
            The (possibly modified) response dict.
        """
        is_response = meta.get("response", False)

        if method == "session/prompt":
            if is_response:
                return await self._handle_post_turn(params, meta)
            return await self._handle_pre_turn(params, meta)

        if method == "session/update":
            return await self._handle_session_update(params, meta)

        # Passthrough for unintercepted methods
        return params

    async def _handle_pre_turn(
        self,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle session/prompt request → pre_turn hooks.

        Args:
            params: The prompt parameters.
            meta: Message metadata.

        Returns:
            Modified params (additional_context injected) or error response.
        """
        agent_name: str = meta.get("agent_name", "")
        prompt: str = ""
        content: Any = params.get("content", [])
        if isinstance(content, str):
            prompt = content
        elif isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                prompt = first.get("text", "")

        for hook in self._hooks:
            result = await hook.run_pre_turn_hooks(
                agent_name=agent_name,
                prompt=prompt,
            )
            if result.get("decision") == "deny":
                return {
                    "error": {
                        "code": -32603,
                        "message": "Blocked by pre_turn hook",
                        "data": {"reason": result.get("reason", "")},
                    }
                }
            additional_context = result.get("additional_context")
            if additional_context:
                content_list: list[Any] = params.get("content", [])
                if isinstance(content_list, list):
                    content_list.insert(
                        0,
                        {"type": "text", "text": additional_context},
                    )
                    params["content"] = content_list
        return params

    async def _handle_post_turn(
        self,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle session/prompt response → post_turn hooks.

        Must NOT fire on individual AgentMessageChunk chunks.
        Only fires when the full JSON-RPC response is received.

        Args:
            params: The response parameters.
            meta: Message metadata (must contain "response": True).

        Returns:
            Modified response (modified_output applied).
        """
        agent_name: str = meta.get("agent_name", "")
        prompt: str = meta.get("prompt", "")

        for hook in self._hooks:
            result = await hook.run_post_turn_hooks(
                agent_name=agent_name,
                prompt=prompt,
                result=params,
                duration_ms=meta.get("duration_ms", 0.0),
            )
            modified_output = result.get("modified_output")
            if modified_output is not None and isinstance(modified_output, dict):
                params = modified_output
        return params

    async def _handle_session_update(
        self,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle session/update → pre_tool_use / post_tool_use hooks.

        Routes based on update type:
        - ToolCallStart → pre_tool_use
        - ToolCallComplete → post_tool_use
        - AgentMessageChunk → passthrough (no hook firing)

        Args:
            params: The update parameters.
            meta: Message metadata.

        Returns:
            Modified params or error response.
        """
        update: Any = params.get("update", params)
        update_type: str = ""
        if isinstance(update, dict):
            update_type = update.get("type", "")

        # ToolCallStart → pre_tool_use
        if "ToolCallStart" in update_type or update_type == "tool_call_start":
            tool_name: str = ""
            tool_input: dict[str, Any] = {}
            if isinstance(update, dict):
                tool_name = update.get("tool_call_id", "")
                raw_input = update.get("raw_input", {})
                if isinstance(raw_input, dict):
                    tool_input = raw_input
            agent_name: str = meta.get("agent_name", "")
            for hook in self._hooks:
                result = await hook.run_pre_tool_hooks(
                    agent_name=agent_name,
                    tool_name=tool_name,
                    tool_input=tool_input,
                )
                if result.get("decision") == "deny":
                    return {
                        "error": {
                            "code": -32603,
                            "message": f"Blocked by pre_tool_use hook for {tool_name}",
                            "data": {"reason": result.get("reason", "")},
                        }
                    }
                modified_input = result.get("modified_input")
                if modified_input is not None and isinstance(update, dict):
                    update["raw_input"] = modified_input
            return params

        # ToolCallComplete → post_tool_use
        if "ToolCallComplete" in update_type or update_type == "tool_call_complete":
            tc_tool_name = ""
            tc_tool_input: dict[str, Any] = {}
            tc_tool_output: Any = None
            if isinstance(update, dict):
                tc_tool_name = update.get("tool_call_id", "")
                raw_input = update.get("raw_input", {})
                if isinstance(raw_input, dict):
                    tc_tool_input = raw_input
                tc_tool_output = update.get("raw_output")
            agent_name = meta.get("agent_name", "")
            for hook in self._hooks:
                result = await hook.run_post_tool_hooks(
                    agent_name=agent_name,
                    tool_name=tc_tool_name,
                    tool_input=tc_tool_input,
                    tool_output=tc_tool_output,
                    duration_ms=meta.get("duration_ms", 0.0),
                )
                modified_output = result.get("modified_output")
                if modified_output is not None and isinstance(update, dict):
                    update["raw_output"] = modified_output
            return params

        # AgentMessageChunk and other updates → passthrough (no hook firing)
        return params
