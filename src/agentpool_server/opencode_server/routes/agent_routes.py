"""Agent, command, MCP, LSP, formatter, and logging routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException
import httpx
from llmling_models.auth.anthropic_auth import (
    AnthropicTokenStore,
    build_authorization_url,
    exchange_code_for_token,
    generate_pkce,
)
from pydantic import BaseModel, HttpUrl

from agentpool.mcp_server.manager import MCPManager
from agentpool.resource_providers import AggregatingResourceProvider
from agentpool_config.mcp_server import (
    SSEMCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)
from agentpool_server.opencode_server.dependencies import StateDep
from agentpool_server.opencode_server.models import (
    Agent,
    AuthInfo,
    Command,
    LogRequest,
    McpAuthorizationResponse,
    McpResource,
    MCPStatus,
    ProviderAuthAuthorization,
    ProviderAuthMethod,
    Session,
    SkillInfo,
    WorktreeCreateRequest,
    WorktreeInfo,
    WorktreeRemoveRequest,
    WorktreeResetRequest,
)
from agentpool_server.opencode_server.models.diagnostics import FormatterStatus
from agentpool_server.opencode_server.models.events import LspStatus


if TYPE_CHECKING:
    from agentpool.common_types import MCPConnectionStatus
    from agentpool_server.opencode_server.models.mcp import (
        MCPConnectionStatus as OpenCodeMCPConnectionStatus,
    )


router = APIRouter(tags=["agent"])


@router.get("/agent")
async def list_agents(state: StateDep) -> list[Agent]:
    """List available agents from the AgentPool.

    Returns all agents with their configurations, suitable for the agent
    switcher UI. Agents are marked as primary (visible in switcher) or
    subagent (hidden, used internally).
    """
    pool = state.agent.agent_pool
    assert pool is not None, "AgentPool is not initialized"
    agents = [
        Agent(
            name=name,
            description=agent.description or f"Agent: {name}",
            # model=ModelRef(model_id=agent.model_name or "unknown", provider_id=""),
            mode="primary" if agent == state.agent else "subagent",
            default=(name == pool.main_agent.name),  # Default agent from pool
        )
        for name, agent in pool.all_agents.items()
    ]
    return (
        agents
        if agents
        else [Agent(name="default", description="Default agent", mode="primary", default=True)]
    )


@router.get("/skill")
async def list_skills(state: StateDep) -> list[SkillInfo]:
    """List all available skills.

    Skills are specialized capabilities available to agents.
    Currently returns an empty list as AgentPool doesn't have a skills system.
    """
    _ = state
    return []


@router.get("/command")
async def list_commands(state: StateDep) -> list[Command]:
    """List available slash commands.

    Commands are derived from MCP prompts available to the agent.
    """
    try:
        prompts = await state.agent.tools.list_prompts()
        return [Command(name=p.name, description=p.description or "") for p in prompts]
    except Exception:  # noqa: BLE001
        return []


@router.get("/mcp")
async def get_mcp_status(state: StateDep) -> dict[str, MCPStatus]:
    """Get MCP server status.

    Returns status for each connected MCP server.
    """
    # Use agent's get_mcp_server_info method which handles different agent types
    server_info = await state.agent.get_mcp_server_info()

    # Convert MCPServerStatus dataclass to MCPStatus response model
    return {
        name: MCPStatus(
            name=status.name,
            status=to_opencode_mcp_status(status.status),
            error=status.error,
        )
        for name, status in server_info.items()
    }


def to_opencode_mcp_status(status: MCPConnectionStatus) -> OpenCodeMCPConnectionStatus:
    mapping: dict[MCPConnectionStatus, OpenCodeMCPConnectionStatus] = {
        "connected": "connected",
        "disconnected": "disconnected",
        "error": "error",
        "pending": "disconnected",
        "failed": "error",
        "needs-auth": "disconnected",
        "disabled": "disconnected",
    }
    return mapping[status]


class AddMCPServerRequest(BaseModel):
    """Request to add an MCP server dynamically."""

    command: str | None = None
    """Command to run (for stdio servers)."""

    args: list[str] | None = None
    """Arguments for the command."""

    url: str | None = None
    """URL for HTTP/SSE servers."""

    env: dict[str, str] | None = None
    """Environment variables for the server."""


@router.post("/mcp")
async def add_mcp_server(request: AddMCPServerRequest, state: StateDep) -> MCPStatus:
    """Add an MCP server dynamically.

    Supports stdio servers (command + args) or HTTP/SSE servers (url).
    """
    # Build the config based on request
    # Note: client_id is auto-generated from command/url, custom names not supported
    config: SSEMCPServerConfig | StdioMCPServerConfig | StreamableHTTPMCPServerConfig
    if request.url:
        # HTTP-based server
        if request.url.endswith("/sse"):
            config = SSEMCPServerConfig(url=HttpUrl(request.url))
        else:
            config = StreamableHTTPMCPServerConfig(url=HttpUrl(request.url))
    elif request.command:  # Stdio server
        args = request.args or []
        config = StdioMCPServerConfig(command=request.command, args=args, env=request.env)
    else:
        detail = "Must provide either 'command' (for stdio) or 'url' (for HTTP/SSE)"
        raise HTTPException(status_code=400, detail=detail)

    # Find the MCPManager and add the server
    manager: MCPManager | None = None
    for provider in state.agent.tools.external_providers:
        match provider:
            case MCPManager():
                manager = provider
                break
            case AggregatingResourceProvider():
                for nested in provider.providers:
                    if isinstance(nested, MCPManager):
                        manager = nested
                        break

    if manager is None:
        raise HTTPException(status_code=400, detail="No MCP manager available")

    try:
        await manager.setup_server(config, add_to_config=True)
        return MCPStatus(name=config.client_id, status="connected")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add MCP server: {e}") from e


def _find_mcp_manager(state: Any) -> MCPManager | None:
    """Find the MCPManager from the agent's tool providers."""
    for provider in state.agent.tools.external_providers:
        match provider:
            case MCPManager():
                return provider
            case AggregatingResourceProvider():
                for nested in provider.providers:
                    if isinstance(nested, MCPManager):
                        return nested
    return None


@router.post("/mcp/{name}/connect")
async def connect_mcp_server(name: str, state: StateDep) -> bool:
    """Connect (start) an MCP server by name.

    Finds the server config and sets up the connection via MCPManager.
    """
    manager = _find_mcp_manager(state)
    if manager is None:
        raise HTTPException(status_code=400, detail="No MCP manager available")
    # Find matching server config
    config = next((s for s in manager.servers if s.client_id == name), None)
    if config is None:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {name}")
    try:
        await manager.setup_server(config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect: {e}") from e
    else:
        return True


@router.post("/mcp/{name}/disconnect")
async def disconnect_mcp_server(name: str, state: StateDep) -> bool:
    """Disconnect (stop) an MCP server by name.

    Removes the provider from the manager's active providers.
    """
    manager = _find_mcp_manager(state)
    if manager is None:
        raise HTTPException(status_code=400, detail="No MCP manager available")
    # Find and remove the matching provider
    provider = next((p for p in manager.providers if p.name.endswith(f"_{name}")), None)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {name}")
    try:
        await provider.__aexit__(None, None, None)
        manager.providers.remove(provider)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to disconnect: {e}") from e
    else:
        return True


@router.post("/mcp/{name}/auth")
async def start_mcp_auth(name: str, state: StateDep) -> McpAuthorizationResponse:
    """Start OAuth authentication flow for an MCP server.

    Returns the authorization URL to open in a browser.
    """
    _ = state
    # MCP OAuth is not yet supported in AgentPool's MCP implementation
    raise HTTPException(status_code=501, detail=f"MCP OAuth not yet supported for: {name}")


@router.post("/mcp/{name}/auth/callback")
async def mcp_auth_callback(
    name: str,
    state: StateDep,
    code: str | None = None,
) -> MCPStatus:
    """Complete OAuth authentication for an MCP server."""
    _ = state, code
    raise HTTPException(status_code=501, detail=f"MCP OAuth not yet supported for: {name}")


@router.post("/mcp/{name}/auth/authenticate")
async def mcp_auth_authenticate(name: str, state: StateDep) -> MCPStatus:
    """Start OAuth flow and wait for callback (opens browser)."""
    _ = state
    raise HTTPException(status_code=501, detail=f"MCP OAuth not yet supported for: {name}")


@router.delete("/mcp/{name}/auth")
async def remove_mcp_auth(name: str, state: StateDep) -> dict[str, bool]:
    """Remove OAuth credentials for an MCP server."""
    _ = state
    # Stub - no MCP OAuth credential storage yet
    return {"success": True}


@router.post("/log")
async def log(request: LogRequest, state: StateDep) -> bool:
    """Write a log entry.

    TODO: Integrate with proper logging.
    """
    _ = state  # unused for now
    print(f"[{request.level}] {request.service}: {request.message}")
    return True


@router.get("/experimental/resource")
async def list_mcp_resources(state: StateDep) -> dict[str, McpResource]:
    """Get all available MCP resources from connected servers.

    Returns a dictionary mapping resource keys to McpResource objects.
    Keys are formatted as "{client}:{resource_name}" for uniqueness.
    """
    try:
        result: dict[str, McpResource] = {}
        for resource in await state.agent.tools.list_resources():
            # Create unique key: sanitize client and resource names
            client_name = (resource.client or "unknown").replace("/", "_")
            resource_name = resource.name.replace("/", "_")
            result[f"{client_name}:{resource_name}"] = McpResource(
                name=resource.name,
                uri=resource.uri,
                description=resource.description,
                mime_type=resource.mime_type,
                client=resource.client or "unknown",
            )
    except Exception:  # noqa: BLE001
        return {}
    else:
        return result


@router.post("/experimental/worktree")
async def create_worktree(request: WorktreeCreateRequest, state: StateDep) -> WorktreeInfo:
    """Create a new git worktree for isolated agent work."""
    _ = state, request
    raise HTTPException(status_code=501, detail="Worktrees not yet supported")


@router.get("/experimental/worktree")
async def list_worktrees(state: StateDep) -> list[str]:
    """List all sandbox worktree directories."""
    _ = state
    return []


@router.delete("/experimental/worktree")
async def remove_worktree(request: WorktreeRemoveRequest, state: StateDep) -> bool:
    """Remove a git worktree and delete its branch."""
    _ = state, request
    raise HTTPException(status_code=501, detail="Worktrees not yet supported")


@router.post("/experimental/worktree/reset")
async def reset_worktree(request: WorktreeResetRequest, state: StateDep) -> bool:
    """Reset a worktree branch to the primary default branch."""
    _ = state, request
    raise HTTPException(status_code=501, detail="Worktrees not yet supported")


@router.get("/experimental/session")
async def list_sessions_global(
    state: StateDep,
    directory: str | None = None,
    roots: bool | None = None,
    start: int | None = None,
    cursor: int | None = None,
    search: str | None = None,
    limit: int | None = None,
    archived: bool | None = None,
) -> list[Session]:
    """List sessions globally across all projects.

    Supports pagination via cursor (timestamp-based).
    """
    from agentpool_server.opencode_server.converters import session_data_to_opencode

    effective_limit = limit or 100
    sessions: list[Session] = []
    for data in await state.agent.list_sessions(cwd=directory or state.agent.env.cwd):
        session = session_data_to_opencode(data)
        sessions.append(session)
    # Apply filters
    if roots:
        sessions = [s for s in sessions if s.parent_id is None]
    if start is not None:
        sessions = [s for s in sessions if s.time.updated >= start]
    if cursor is not None:
        sessions = [s for s in sessions if s.time.updated < cursor]
    if search:
        lower_search = search.lower()
        sessions = [s for s in sessions if lower_search in s.title.lower()]
    return sessions[:effective_limit]


@router.get("/experimental/tool/ids")
async def list_tool_ids(state: StateDep) -> list[str]:
    """List all available tool IDs.

    Returns a list of tool names that are available to the agent.
    OpenCode expects: Array<string>
    """
    try:
        tools = await state.agent.tools.get_tools()
        return [tool.name for tool in tools]
    except Exception:  # noqa: BLE001
        return []


class ToolListItem(BaseModel):
    """Tool info matching OpenCode SDK ToolListItem type."""

    id: str
    description: str
    parameters: dict[str, Any]


@router.get("/experimental/tool")
async def list_tools_with_schemas(  # noqa: D417
    state: StateDep,
    provider: str | None = None,
    model: str | None = None,
) -> list[ToolListItem]:
    """List tools with their JSON schemas.

    Args:
        provider: Optional provider filter (not used currently)
        model: Optional model filter (not used currently)

    Returns list of tools matching OpenCode's ToolListItem format:
    - id: string
    - description: string
    - parameters: unknown (JSON schema)
    """
    _ = provider, model  # Currently unused, for future filtering

    try:
        result = []
        for tool in await state.agent.tools.get_tools():
            # Extract parameters schema from the OpenAI function schema
            schema = tool.schema
            params = schema.get("function", {}).get("parameters", {})
            item = ToolListItem(id=tool.name, description=tool.description or "", parameters=params)
            result.append(item)
    except Exception:  # noqa: BLE001
        return []
    else:
        return result


@router.get("/lsp")
async def get_lsp_status(state: StateDep) -> list[LspStatus]:
    """Get LSP server status.

    Returns status of all running LSP servers.
    """
    servers: list[LspStatus] = []
    for server_id, server_state in state.lsp_manager._servers.items():
        status: Literal["connected", "error"] = "connected" if server_state.initialized else "error"
        servers.append(
            LspStatus(id=server_id, name=server_id, status=status, root=server_state.root_uri or "")
        )
    return servers


@router.get("/formatter")
async def get_formatter_status(state: StateDep) -> list[FormatterStatus]:
    """Get formatter status.

    Returns empty list - formatters not supported yet.
    """
    _ = state
    return []


@router.get("/provider/auth")
async def get_provider_auth(state: StateDep) -> dict[str, list[ProviderAuthMethod]]:
    """Get provider authentication methods.

    Returns available OAuth providers with their auth methods.
    """
    _ = state
    return {
        "anthropic": [
            ProviderAuthMethod(type="oauth", label="Connect Claude Max/Pro"),
        ],
        "copilot": [
            ProviderAuthMethod(type="oauth", label="Connect GitHub Copilot"),
        ],
    }


# Store for active OAuth flows (in production, use Redis or similar)
_oauth_flows: dict[str, dict[str, Any]] = {}


@router.post("/provider/{provider_id}/oauth/authorize")
async def oauth_authorize(provider_id: str, state: StateDep) -> ProviderAuthAuthorization:
    """Start OAuth authorization flow for a provider.

    Returns URL and instructions for the user to complete authorization.
    """
    _ = state

    if provider_id == "anthropic":
        verifier, challenge = generate_pkce()
        auth_url = build_authorization_url(verifier, challenge)
        # Store verifier for callback
        _oauth_flows[f"anthropic:{verifier}"] = {"verifier": verifier}
        return ProviderAuthAuthorization(
            url=auth_url,
            instructions="Sign in with your Anthropic account and copy the authorization code",
            method="code",
        )

    if provider_id == "copilot":
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://github.com/login/device/code",
                headers={
                    "accept": "application/json",
                    "editor-version": "Neovim/0.6.1",
                    "editor-plugin-version": "copilot.vim/1.16.0",
                    "content-type": "application/json",
                    "user-agent": "GithubCopilot/1.155.0",
                },
                json={"client_id": "Iv1.b507a08c87ecfe98", "scope": "read:user"},
            )
            resp.raise_for_status()
            data = resp.json()

        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_uri = data["verification_uri"]
        # Store device_code for callback
        _oauth_flows[f"copilot:{device_code}"] = {"device_code": device_code}

        return ProviderAuthAuthorization(
            url=verification_uri,
            instructions=f"Enter code: {user_code}",
            method="auto",
        )

    raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")


@router.post("/provider/{provider_id}/oauth/callback")
async def oauth_callback(
    provider_id: str,
    state: StateDep,
    code: str | None = None,
    device_code: str | None = None,
    verifier: str | None = None,
) -> bool:
    """Handle OAuth callback/code exchange.

    For Anthropic: exchanges authorization code for tokens.
    For Copilot: polls for token using device code.
    """
    _ = state

    if provider_id == "anthropic":
        if not code or not verifier:
            raise HTTPException(
                status_code=400, detail="Missing code or verifier for Anthropic OAuth"
            )

        try:
            token = exchange_code_for_token(code, verifier)
            store = AnthropicTokenStore()
            store.save(token)
            # Clean up flow state
            _oauth_flows.pop(f"anthropic:{verifier}", None)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        else:
            return True

    if provider_id == "copilot":
        if not device_code:
            raise HTTPException(status_code=400, detail="Missing device_code for Copilot OAuth")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={
                    "accept": "application/json",
                    "editor-version": "Neovim/0.6.1",
                    "editor-plugin-version": "copilot.vim/1.16.0",
                    "content-type": "application/json",
                    "user-agent": "GithubCopilot/1.155.0",
                },
                json={
                    "client_id": "Iv1.b507a08c87ecfe98",
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            data = resp.json()

        if "error" in data:
            detail = data.get("error_description", data["error"])
            raise HTTPException(status_code=400, detail=detail)

        if data.get("access_token"):
            # Clean up flow state
            _oauth_flows.pop(f"copilot:{device_code}", None)
            # TODO: Store copilot token
            return True

        raise HTTPException(status_code=400, detail="No token received")
    raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")


@router.put("/auth/{provider_id}")
async def set_auth(provider_id: str, info: AuthInfo, state: StateDep) -> bool:
    """Set authentication credentials for a provider.

    Stores the credentials so they can be used for subsequent API calls.
    """
    _ = state
    # Store credentials based on provider type
    if provider_id == "anthropic" and info.token:
        store = AnthropicTokenStore()
        from llmling_models.auth.anthropic_auth import AnthropicOAuthToken

        token = AnthropicOAuthToken(
            access_token=info.token,
            refresh_token=info.refresh or "",
            expires_at=info.expires or 0,
        )
        store.save(token)
        return True
    # For other providers, we'd need provider-specific storage
    # For now, just acknowledge the request
    return True


@router.delete("/auth/{provider_id}")
async def remove_auth(provider_id: str, state: StateDep) -> bool:
    """Remove authentication credentials for a provider."""
    _ = state
    if provider_id == "anthropic":
        store = AnthropicTokenStore()
        store.clear()
        return True
    return True
