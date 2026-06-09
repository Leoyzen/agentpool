"""Pool-level commands for managing agent pools and configurations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.agents.base_agent import BaseAgent
from agentpool_commands.base import NodeCommand


if TYPE_CHECKING:
    from slashed import CommandContext

    from agentpool.messaging.context import NodeContext


class ListPoolsCommand(NodeCommand):
    """List available agent pool configurations.

    Examples:
      /list-pools
    """

    name = "list-pools"
    category = "pool"

    async def execute_command(self, ctx: CommandContext[NodeContext[Any]]) -> None:
        """List available pool configurations.

        Args:
            ctx: Command context with node context
        """
        from agentpool_cli import agent_store

        pool = ctx.context.pool
        if pool is None:
            raise RuntimeError("No pool configured")
        try:
            output_lines = ["## 🏊 Agent Pool Configurations\n"]
            output_lines.append("### 📍 Current Pool")
            # Get config path from context config (works for all agent types)
            current_cfg = pool.manifest.config_file_path
            if current_cfg:
                output_lines.append(f"**Config:** `{current_cfg}`")
            else:
                output_lines.append("**Config:** *(default/built-in)*")
            # Show agents in current pool
            agent_names = list(pool.all_agents.keys())
            output_lines.append(f"**Agents:** {', '.join(f'`{n}`' for n in agent_names)}")
            output_lines.append(f"**Active agent:** `{ctx.context.node.name}`")
            output_lines.append("")
            # Show stored configurations
            output_lines.append("### 💾 Stored Configurations")
            stored_configs = agent_store.list_configs()
            active_config = agent_store.get_active()

            if not stored_configs:
                output_lines.append("*No stored configurations*")
                output_lines.append("")
                output_lines.append("Use `agentpool add <name> <path>` to add configurations.")
            else:
                # Build markdown table
                output_lines.append("| Name | Path |")
                output_lines.append("|------|------|")
                for name, path in stored_configs:
                    is_active = active_config and active_config.name == name
                    is_current = current_cfg and path == current_cfg
                    markers = []
                    if is_active:
                        markers.append("default")
                    if is_current:
                        markers.append("current")
                    name_col = f"{name} ({', '.join(markers)})" if markers else name
                    output_lines.append(f"| {name_col} | `{path}` |")

            output_lines.append("")
            output_lines.append("*Use `/set-pool <name>` or `/set-pool <path>` to switch pools.*")

            await ctx.output.print("\n".join(output_lines))

        except Exception as e:  # noqa: BLE001
            await ctx.output.print(f"❌ **Error listing pools:** {e}")


class CompactCommand(NodeCommand):
    """Compact the conversation history to reduce context size.

    Uses the configured compaction pipeline from the agent pool manifest,
    or falls back to a default summarizing pipeline.

    Options:
      --preset <name>   Use a specific preset (minimal, balanced, summarizing)

    Examples:
      /compact
      /compact --preset=minimal
    """

    name = "compact"
    category = "pool"

    async def execute_command(
        self,
        ctx: CommandContext[NodeContext[Any]],
        *,
        preset: str | None = None,
    ) -> None:
        """Compact the conversation history.

        Args:
            ctx: Command context with node context
            preset: Optional preset name (minimal, balanced, summarizing)
        """
        from agentpool.agents.base_agent import BaseAgent

        # Get agent from context
        agent = ctx.context.node
        if not isinstance(agent, BaseAgent):
            await ctx.output.print(
                "❌ **This command requires an agent with conversation history**"
            )
            return

        # Check if there's any history to compact
        if not agent.conversation.get_history():
            await ctx.output.print("📭 **No message history to compact**")
            return

        try:
            # Get compaction pipeline
            from agentpool.messaging.compaction import (
                balanced_context,
                minimal_context,
                summarizing_context,
            )

            pipeline = None

            # Check for preset override
            if preset:
                match preset.lower():
                    case "minimal":
                        pipeline = minimal_context()
                    case "balanced":
                        pipeline = balanced_context()
                    case "summarizing":
                        pipeline = summarizing_context()
                    case _:
                        await ctx.output.print(
                            f"⚠️ **Unknown preset:** `{preset}`\n"
                            "Available: minimal, balanced, summarizing"
                        )
                        return

            # Fall back to pool's configured pipeline
            if pipeline is None and ctx.context.pool is not None:
                pipeline = ctx.context.pool.compaction_pipeline

            # Fall back to default summarizing pipeline
            if pipeline is None:
                pipeline = summarizing_context()

            await ctx.output.print("🔄 **Compacting conversation history...**")

            # Apply the pipeline using shared helper
            from agentpool.messaging.compaction import compact_conversation

            original_count, compacted_count = await compact_conversation(
                pipeline, agent.conversation
            )
            reduction = original_count - compacted_count

            await ctx.output.print(
                f"✅ **Compaction complete**\n"
                f"- Messages: {original_count} → {compacted_count} ({reduction} removed)\n"
                f"- Reduction: {reduction / original_count * 100:.1f}%"
                if original_count > 0
                else "✅ **Compaction complete** (no messages)"
            )

        except Exception as e:  # noqa: BLE001
            await ctx.output.print(f"❌ **Error compacting history:** {e}")


class SpawnCommand(NodeCommand):
    """Spawn a subagent to execute a specific task.

    The subagent runs through the SessionPool and its events are automatically
    routed to the frontend via EventBus ``scope="descendants"`` subscription.
    No manual event wrapping is performed by the business layer.

    Examples:
      /spawn agent-name "task description"
      /spawn code-reviewer "Review main.py for bugs"
    """

    name = "spawn"
    category = "pool"

    @classmethod
    def supports_node(cls, node: Any) -> bool:
        """Only available when running from an agent (needs events)."""
        return isinstance(node, BaseAgent)

    async def execute_command(
        self,
        ctx: CommandContext[NodeContext[Any]],
        agent_name: str,
        task_prompt: str,
    ) -> None:
        """Spawn a subagent to execute a task.

        Args:
            ctx: Command context with node context
            agent_name: Name of the agent to spawn
            task_prompt: Task prompt for the subagent
        """
        from agentpool.agents.events import SpawnSessionStart
        from agentpool.common_types import SupportsRunStream

        pool = ctx.context.pool
        if pool is None:
            await ctx.output.print("❌ **No agent pool available**")
            return

        session_pool = pool.session_pool
        if session_pool is None:
            await ctx.output.print("❌ **SessionPool is required for spawn command**")
            return

        if agent_name not in pool.nodes:
            available = list(pool.nodes.keys())
            await ctx.output.print(
                f"❌ **Agent** `{agent_name}` **not found**\n\n"
                f"Available agents: {', '.join(available)}"
            )
            return
        agent = pool.nodes[agent_name]
        # Check if node supports streaming
        if not isinstance(agent, SupportsRunStream):
            await ctx.output.print(f"❌ **Agent** `{agent_name}` **does not support streaming**")
            return

        # Get parent session ID from the active run context
        parent_session_id = ""
        agent_ctx = getattr(ctx.context, "run_ctx", None)
        if agent_ctx is not None:
            parent_session_id = getattr(agent_ctx, "session_id", "") or ""

        child_session_id = await ctx.context.agent.get_context().create_child_session(
            agent_name=agent_name,
            agent_type=agent.agent_type,
            parent_session_id=parent_session_id,
            source_name=agent_name,
            source_type="agent",
            depth=1,
        )

        # Emit SpawnSessionStart so the protocol layer can set up the child session UI
        # Emit SpawnSessionStart so the protocol layer can detect child session
        # creation. All other stream events flow through TurnRunner → EventBus
        # and reach the frontend via protocol-layer ``scope="descendants"``
        # subscription — no manual business-layer forwarding is required.
        spawn_event = SpawnSessionStart(
            child_session_id=child_session_id,
            parent_session_id=parent_session_id,
            spawn_mechanism="spawn",
            source_name=agent_name,
            source_type="agent",
            depth=1,
            description=f"Spawn {agent_name}",
            metadata={"prompt": task_prompt[:200]} if task_prompt else {},
        )
        await ctx.context.agent.get_context().events.emit_event(spawn_event)

        # Run the subagent through SessionPool — events flow to EventBus automatically
        async for _event in session_pool.run_stream(child_session_id, task_prompt):
            # Events are consumed to drive the stream; they reach the protocol layer
            # via EventBus descendants subscription.
            pass
