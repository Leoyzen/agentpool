"""Command for running agents as an OpenCode-compatible server.

This creates an HTTP server that implements the OpenCode API protocol,
allowing OpenCode TUI and SDK clients to interact with AgentPool agents.

Configuration is resolved from multiple layers (in precedence order):
1. Global config (~/.config/agentpool/agentpool.yml)
2. Custom config (AGENTPOOL_CONFIG env var)
3. Project config (agentpool.yml in project/git root)
4. Explicit CLI argument (highest precedence)
5. Built-in fallback (only if no agents defined elsewhere)
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from platformdirs import user_log_path
import typer as t

from agentpool_cli import log


logger = log.get_logger(__name__)


def opencode_command(
    config: Annotated[str | None, t.Argument(help="Path to agent configuration (optional)")] = None,
    host: Annotated[
        str,
        t.Option("--host", "-h", help="Host to bind to"),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        t.Option("--port", "-p", help="Port to listen on"),
    ] = 4096,
    agent: Annotated[
        str | None,
        t.Option(
            "--agent",
            help="Name of specific agent to use (defaults to pool's default agent)",
        ),
    ] = None,
    working_dir: Annotated[
        str | None,
        t.Option(
            "--working-dir",
            "-w",
            help="Working directory for file operations (defaults to current directory)",
        ),
    ] = None,
) -> None:
    """Run agents as an OpenCode-compatible HTTP server.

    This creates an HTTP server implementing the OpenCode API protocol,
    enabling your AgentPool agents to work with OpenCode TUI and SDK clients.

    Configuration Layers (merged in order, later overrides earlier):

    1. Global config - ~/.config/agentpool/agentpool.yml for user preferences
    2. Custom config - AGENTPOOL_CONFIG env var for CI/deployment overrides
    3. Project config - agentpool.yml in project/git root for project-specific settings
    4. Explicit config - CLI argument (highest precedence)
    5. Built-in fallback - Only used if no agents defined in any layer

    Agent Selection:
    Use --agent to specify which agent to use by name. Without this option,
    the pool's default agent is used (set via 'default_agent' in config,
    or falls back to the first agent).
    """
    from agentpool import AgentPool, log as ap_log
    from agentpool.config_resources import CLAUDE_CODE_ASSISTANT
    from agentpool.models.manifest import AgentsManifest
    from agentpool_config.context import ConfigContextManager
    from agentpool_config.resolution import resolve_config
    from agentpool_server.opencode_server.server import OpenCodeServer

    # Resolve configuration from all layers FIRST (before logging)
    # fallback_config is only used if no agents defined elsewhere
    try:
        resolved = resolve_config(
            explicit_path=config,
            fallback_config=CLAUDE_CODE_ASSISTANT,
        )
    except ValueError as e:
        raise t.BadParameter(str(e)) from e

    # Load manifest from merged config data with config context for path resolution
    # Config context must be maintained for AgentPool initialization (for relative path resolution)
        try:
            if resolved.primary_path:
                # 为 manifest 和每个 agent/team 设置 config_file_path
                # 这对于相对路径解析（如 file prompts）至关重要
                def update_with_path(nodes: dict[str, Any]) -> dict[str, Any]:
                    return {
                        name: config.model_copy(update={"config_file_path": resolved.primary_path})
                        for name, config in nodes.items()
                    }

        manifest = AgentsManifest.model_validate(resolved.data)
        manifest = manifest.model_copy(
            update={
                "config_file_path": resolved.primary_path,
                " AgentsManifest.model_validate(被移除",
                "agents": update_with_path(manifest.agents),
                "teams": update_with_path(manifest.teams),
                }
            )
            logger.info("Server starting", url=f"http://{host}:{port}")
            await server.run_async()

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("OpenCode server shutdown requested")
    except Exception as e:
        logger.exception("OpenCode server error")
        raise t.Exit(1) from e


if __name__ == "__main__":
    t.run(opencode_command)
