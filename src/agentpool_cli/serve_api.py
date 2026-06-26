"""Command for running agents as a completions API server."""

from __future__ import annotations

import os
from typing import Annotated, Any

import typer as t

from agentpool_cli import resolve_agent_config
from agentpool_cli.log import get_logger


logger = get_logger(__name__)


def api_command(
    ctx: t.Context,
    config: Annotated[str | None, t.Argument(help="Path to agent configuration")] = None,
    host: Annotated[str, t.Option(help="Host to bind server to")] = "localhost",
    port: Annotated[int, t.Option(help="Port to listen on")] = 8000,
    cors: Annotated[bool, t.Option(help="Enable CORS")] = True,
    show_messages: Annotated[
        bool, t.Option("--show-messages", help="Show message activity (deprecated, no-op)")
    ] = False,
    docs: Annotated[bool, t.Option(help="Enable API documentation")] = True,
) -> None:
    """Run agents as a completions API server.

    This creates an OpenAI-compatible API server that makes your agents available
    through a standard completions API interface.
    """
    import uvicorn

    from agentpool import AgentPool, AgentsManifest
    from agentpool_config.context import ConfigContextManager
    from agentpool_server.openai_api_server.server import OpenAIAPIServer

    logger.info("Server PID", pid=os.getpid())

    try:
        config_path = resolve_agent_config(config)
    except ValueError as e:
        msg = str(e)
        raise t.BadParameter(msg) from e
    with ConfigContextManager(config_path):
        manifest = AgentsManifest.from_file(config_path)
        if config_path:
            def update_with_path(nodes: dict[str, Any]) -> dict[str, Any]:
                return {
                    name: node_config.model_copy(update={"config_file_path": config_path})
                    for name, node_config in nodes.items()
                }

            manifest = manifest.model_copy(
                update={
                    "config_file_path": config_path,
                    "agents": update_with_path(manifest.agents),
                    "teams": update_with_path(manifest.teams),
                }
            )

        # Keep AgentPool initialization inside the config context so custom
        # providers can resolve relative schema/prompt paths against the YAML directory.
        pool = AgentPool(manifest)

    # show_messages is disabled: agent instances are no longer created at pool level.
    # Session-level event monitoring is available via EventBus instead.

    server = OpenAIAPIServer(pool, cors=cors, docs=docs)

    # Get log level from the global context
    log_level = ctx.obj.get("log_level", "info") if ctx.obj else "info"
    uvicorn.run(server.app, host=host, port=port, log_level=log_level.lower())


if __name__ == "__main__":
    import typer

    typer.run(api_command)
