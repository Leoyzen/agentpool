from __future__ import annotations

from typing import TYPE_CHECKING

from agentpool.agents.context import AgentContext  # noqa: TC001


if TYPE_CHECKING:
    from clawd_code_sdk.models.server_info import ClaudeCodeCommandInfo
    from slashed import Command, CommandContext


def create_claude_code_command(cmd_info: ClaudeCodeCommandInfo) -> Command:
    """Create a slashed Command from Claude Code command info.

    Args:
        cmd_info: Command info dict with 'name', 'description', 'argumentHint'

    Returns:
        A slashed Command that executes via Claude Code
    """
    from clawd_code_sdk.models import (
        AssistantMessage,
        LocalCommandOutputMessage,
        ResultErrorMessage,
        ResultSuccessMessage,
        TextBlock,
        UserMessage,
    )
    from slashed import Command

    name = cmd_info.name
    # Handle MCP commands - they have " (MCP)" suffix in Claude Code
    category = "claude_code"
    if name.endswith(" (MCP)"):
        name = f"mcp:{name.replace(' (MCP)', '')}"
        category = "mcp"

    async def execute_command(
        ctx: CommandContext[AgentContext],
        args: list[str],
        kwargs: dict[str, str],
    ) -> None:
        """Execute the Claude Code slash command."""
        # Build command string
        args_str = " ".join(args) if args else ""
        if kwargs:
            kwargs_str = " ".join(f"{k}={v}" for k, v in kwargs.items())
            args_str = f"{args_str} {kwargs_str}".strip()
        # Execute via agent run - slash commands go through as prompts
        from agentpool.agents.claude_code_agent.claude_code_agent import ClaudeCodeAgent

        agent = ctx.context.agent
        assert isinstance(agent, ClaudeCodeAgent)
        if not agent._client:
            return
        await agent._client.query(f"/{name} {args_str}".strip())
        async for msg in agent._client.receive_response():
            match msg:
                case AssistantMessage():
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            await ctx.print(block.text)
                case LocalCommandOutputMessage(content=content):
                    await ctx.print(content)
                # Some commands (e.g. /compact) still use legacy XML-tagged
                # output in UserMessage instead of LocalCommandOutputMessage.
                case UserMessage() if parsed := msg.parse_command_output():
                    await ctx.print(parsed)
                case ResultSuccessMessage(result=result) if result:
                    await ctx.print(result)
                case ResultErrorMessage(subtype=subtype, errors=errors):
                    await ctx.print(f"Error: {subtype}")
                    if errors:
                        await ctx.print(f"Errors: {errors}")

    return Command.from_raw(
        execute_command,
        name=name,
        description=cmd_info.description or f"Claude Code command: {name}",
        category=category,
        usage=cmd_info.argument_hint,
    )
