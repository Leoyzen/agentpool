from __future__ import annotations

from typing import TYPE_CHECKING

from codex_adapter.models import HttpMcpServer, StdioMcpServer


if TYPE_CHECKING:
    from codex_adapter.models import McpServerConfig


def kebab_to_camel(s: str) -> str:
    """Convert kebab-case to camelCase."""
    parts = s.split("-")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def mcp_config_to_toml_inline(name: str, config: McpServerConfig) -> str:
    """Convert MCP server config to TOML inline table format."""
    match config:
        case StdioMcpServer(command=command, args=args, env=env, enabled=enabled):
            # Build stdio config
            parts = [f'command = "{command}"']
            if args:
                args_str = ", ".join(f'"{arg}"' for arg in args)
                parts.append(f"args = [{args_str}]")
            if env:
                # env as inline table
                env_items = ", ".join(f'{k} = "{v}"' for k, v in env.items())
                parts.append(f"env = {{{env_items}}}")
            if not enabled:
                parts.append("enabled = false")
            return f"mcp_servers.{name}={{{', '.join(parts)}}}"

        case HttpMcpServer(
            url=url,
            bearer_token_env_var=bearer_token_env_var,
            http_headers=http_headers,
            enabled=enabled,
        ):
            # Build HTTP config
            parts = [f'url = "{url}"']
            if bearer_token_env_var:
                parts.append(f'bearer_token_env_var = "{bearer_token_env_var}"')
            if http_headers:
                # headers as inline table
                headers_items = ", ".join(f'{k} = "{v}"' for k, v in http_headers.items())
                parts.append(f"http_headers = {{{headers_items}}}")
            if not enabled:
                parts.append("enabled = false")
            return f"mcp_servers.{name}={{{', '.join(parts)}}}"
        case _:
            raise ValueError(f"Unsupported MCP server config type: {type(config)}")
