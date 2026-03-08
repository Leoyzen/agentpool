"""Async HTTP client for the OpenCode server API.

Provides typed access to all OpenCode REST and SSE endpoints, returning
OpenCode SDK models directly.

Usage:
    async with OpenCodeClient("http://localhost:3000") as client:
        session = await client.create_session()
        await session.send_message(request)
        messages = await session.list_messages()

        async for event in client.events():
            print(event)
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Self

import anyenv
import httpx
from pydantic import TypeAdapter

from opencode_sdk.models.app import App, HealthResponse, PathInfo, Project, VcsInfo
from opencode_sdk.models.common import FileDiff
from opencode_sdk.models.config import Config
from opencode_sdk.models.events import Event, PermissionAskedProperties
from opencode_sdk.models.mcp import MCPStatus
from opencode_sdk.models.message import MessageWithParts
from opencode_sdk.models.question import QuestionRequest
from opencode_sdk.models.session import Session, SessionStatus, Todo


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from opencode_sdk.models.agent import Agent, Command, SkillInfo
    from opencode_sdk.models.events import PermissionReplyRequest
    from opencode_sdk.models.mcp import AddMcpServerRequest, LogLevel
    from opencode_sdk.models.message import CommandRequest, MessageRequest, ShellRequest
    from opencode_sdk.models.question import QuestionReply
    from opencode_sdk.models.session import (
        SessionCreateRequest,
        SessionForkRequest,
        SessionInitRequest,
        SessionUpdateRequest,
        SummarizeRequest,
    )


_event_adapter: TypeAdapter[Event] = TypeAdapter(Event)


# ── Session handle ────────────────────────────────────────────────────


class SessionHandle:
    """Handle for interacting with a specific OpenCode session.

    Wraps session data + client reference so all session-scoped operations
    can be called directly without passing session_id everywhere.

    Usage:
        session = await client.create_session()
        await session.send_message(request)
        messages = await session.list_messages()
        await session.abort()
    """

    def __init__(self, client: OpenCodeClient, info: Session) -> None:
        self._client = client
        self.info = info

    @property
    def id(self) -> str:
        return self.info.id

    @property
    def title(self) -> str:
        return self.info.title

    # ── Session lifecycle ─────────────────────────────────────────

    async def refresh(self) -> None:
        """Re-fetch session data from the server."""
        handle = await self._client.session(self.id)
        self.info = handle.info

    async def update(self, request: SessionUpdateRequest) -> None:
        """Update session metadata (e.g. title, archive)."""
        self.info = await self._client.update_session(self.id, request)

    async def delete(self) -> bool:
        """Delete this session."""
        return await self._client.delete_session(self.id)

    async def abort(self) -> bool:
        """Abort this session if busy."""
        return await self._client.abort_session(self.id)

    async def fork(self, request: SessionForkRequest | None = None) -> SessionHandle:
        """Fork this session, returning a new SessionHandle."""
        return await self._client.fork_session(self.id, request)

    async def init(self, request: SessionInitRequest | None = None) -> MessageWithParts:
        """Initialize this session (create AGENTS.md)."""
        return await self._client.init_session(self.id, request)

    async def children(self) -> list[SessionHandle]:
        """Get child sessions as handles."""
        sessions = await self._client.get_session_children(self.id)
        return [SessionHandle(self._client, s) for s in sessions]

    # ── Messages ──────────────────────────────────────────────────

    async def list_messages(self, *, limit: int | None = None) -> list[MessageWithParts]:
        """List messages in this session."""
        return await self._client.list_messages(self.id, limit=limit)

    async def send_message(self, request: MessageRequest) -> MessageWithParts:
        """Send a message and wait for the agent's response."""
        return await self._client.send_message(self.id, request)

    async def send_message_async(self, request: MessageRequest) -> None:
        """Send a message asynchronously (listen to SSE for updates)."""
        return await self._client.send_message_async(self.id, request)

    async def get_message(self, message_id: str) -> MessageWithParts:
        """Get a specific message."""
        return await self._client.get_message(self.id, message_id)

    async def delete_message(self, message_id: str) -> bool:
        """Delete a message and all its parts."""
        return await self._client.delete_message(self.id, message_id)

    # ── Commands / Shell ──────────────────────────────────────────

    async def execute_command(self, request: CommandRequest) -> MessageWithParts:
        """Execute a slash command in this session."""
        return await self._client.execute_command(self.id, request)

    async def shell(self, request: ShellRequest) -> MessageWithParts:
        """Run a shell command in this session."""
        return await self._client.shell(self.id, request)

    async def summarize(self, request: SummarizeRequest | None = None) -> MessageWithParts:
        """Summarize/compact this session."""
        return await self._client.summarize(self.id, request)

    # ── Diffs / Todos ─────────────────────────────────────────────

    async def todos(self) -> list[Todo]:
        """Get todos for this session."""
        return await self._client.get_session_todos(self.id)

    async def diff(self) -> list[FileDiff]:
        """Get file diffs for this session."""
        return await self._client.get_session_diff(self.id)

    # ── Share / Revert ────────────────────────────────────────────

    async def share(self) -> None:
        """Share this session (create shareable link)."""
        self.info = await self._client.share_session(self.id)

    async def unshare(self) -> None:
        """Remove session sharing."""
        self.info = await self._client.unshare_session(self.id)

    async def revert(self, *, message_id: str, part_id: str | None = None) -> None:
        """Revert this session to a specific message."""
        self.info = await self._client.revert_session(
            self.id, message_id=message_id, part_id=part_id
        )

    async def unrevert(self) -> None:
        """Undo a revert."""
        self.info = await self._client.unrevert_session(self.id)

    # ── Permissions ───────────────────────────────────────────────

    async def list_permissions(self) -> list[PermissionAskedProperties]:
        """Get pending permission requests for this session."""
        return await self._client.list_permissions(self.id)

    async def reply_permission(
        self,
        permission_id: str,
        reply: PermissionReplyRequest,
    ) -> bool:
        """Reply to a permission request."""
        return await self._client.reply_permission(self.id, permission_id, reply)

    def __repr__(self) -> str:
        return f"SessionHandle(id={self.id!r}, title={self.title!r})"


# ── Client ────────────────────────────────────────────────────────────


class OpenCodeClient:
    """Async HTTP client for the OpenCode server API.

    All methods return OpenCode SDK models — no agentpool-specific types.
    Uses httpx for HTTP and SSE streaming.

    Session-scoped operations are available both as flat methods on the client
    (taking ``session_id``) and as methods on :class:`SessionHandle` objects
    returned by :meth:`create_session` and :meth:`session`.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:3000",
        *,
        timeout: float = 30.0,
        sse_timeout: float | None = None,
    ) -> None:
        """Initialize the OpenCode client.

        Args:
            base_url: Base URL of the OpenCode server.
            timeout: Default timeout for HTTP requests in seconds.
            sse_timeout: Timeout for SSE connections (None = no timeout).
        """
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._sse_timeout = sse_timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Return the active httpx client, raising if not connected."""
        if self._client is None:
            msg = "Client not connected. Use 'async with OpenCodeClient(...) as client:'"
            raise RuntimeError(msg)
        return self._client

    # ── Helpers ───────────────────────────────────────────────────────

    async def _get(self, path: str, **params: Any) -> Any:
        """GET request, returning parsed JSON."""
        filtered = {k: v for k, v in params.items() if v is not None}
        resp = await self.client.get(path, params=filtered)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, json: Any = None) -> Any:
        """POST request, returning parsed JSON (or None for 204)."""
        resp = await self.client.post(path, json=json)
        resp.raise_for_status()
        if resp.status_code == HTTPStatus.NO_CONTENT:
            return None
        return resp.json()

    async def _patch(self, path: str, json: Any = None) -> Any:
        """PATCH request, returning parsed JSON."""
        resp = await self.client.patch(path, json=json)
        resp.raise_for_status()
        return resp.json()

    async def _delete(self, path: str) -> Any:
        """DELETE request, returning parsed JSON."""
        resp = await self.client.delete(path)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _dump(model: Any) -> dict[str, Any]:
        """Serialize an OpenCode model to a JSON-compatible dict."""
        result: dict[str, Any] = model.model_dump(by_alias=True, exclude_none=True)
        return result

    # ── Global / Health ───────────────────────────────────────────────

    async def health(self) -> HealthResponse:
        """Check server health."""
        data = await self._get("/global/health")
        return HealthResponse.model_validate(data)

    async def get_global_config(self) -> Config:
        """Get global configuration."""
        data = await self._get("/global/config")
        return Config.model_validate(data)

    async def update_global_config(self, config: Config) -> Config:
        """Update global configuration."""
        data = await self._patch("/global/config", json=self._dump(config))
        return Config.model_validate(data)

    async def dispose(self) -> bool:
        """Dispose all instances and release resources."""
        data = await self._post("/global/dispose")
        return bool(data)

    # ── SSE Events ────────────────────────────────────────────────────

    async def events(self, *, wrap_payload: bool = False) -> AsyncIterator[Event]:
        """Stream SSE events from the server.

        Args:
            wrap_payload: If True, use /global/event (payload-wrapped);
                otherwise use /event (raw events).

        Yields:
            Parsed Event models.
        """
        path = "/global/event" if wrap_payload else "/event"
        timeout = httpx.Timeout(self._timeout, read=self._sse_timeout)
        async with (
            httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as sse_client,
            sse_client.stream("GET", path) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                json_data: dict[str, Any] = anyenv.load_json(raw, return_type=dict)
                if wrap_payload and "payload" in json_data:
                    json_data = json_data["payload"]
                yield _event_adapter.validate_python(json_data)

    # ── App / Project ─────────────────────────────────────────────────

    async def get_app(self) -> App:
        """Get application info."""
        data = await self._get("/app")
        return App.model_validate(data)

    async def list_projects(self) -> list[Project]:
        """List all projects."""
        data = await self._get("/project")
        return [Project.model_validate(p) for p in data]

    async def get_current_project(self) -> Project:
        """Get the current project."""
        data = await self._get("/project/current")
        return Project.model_validate(data)

    async def get_path_info(self) -> PathInfo:
        """Get path information (cwd, root, etc.)."""
        data = await self._get("/path")
        return PathInfo.model_validate(data)

    async def get_vcs_info(self) -> VcsInfo:
        """Get VCS (git) information."""
        data = await self._get("/vcs")
        return VcsInfo.model_validate(data)

    # ── Config / Providers ────────────────────────────────────────────

    async def get_config(self) -> Config:
        """Get configuration."""
        data = await self._get("/config")
        return Config.model_validate(data)

    async def update_config(self, config: Config) -> Config:
        """Update configuration."""
        data = await self._patch("/config", json=self._dump(config))
        return Config.model_validate(data)

    # ── Sessions ──────────────────────────────────────────────────────

    async def list_sessions(
        self,
        *,
        directory: str | None = None,
        roots: bool | None = None,
        start: int | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[SessionHandle]:
        """List sessions.

        Args:
            directory: Filter by project directory.
            roots: Only return root sessions (no parent).
            start: Filter sessions updated on or after this timestamp (ms).
            search: Filter by title (case-insensitive).
            limit: Maximum number of sessions to return.
        """
        data = await self._get(
            "/session",
            directory=directory,
            roots=roots,
            start=start,
            search=search,
            limit=limit,
        )
        return [SessionHandle(self, Session.model_validate(s)) for s in data]

    async def create_session(
        self,
        request: SessionCreateRequest | None = None,
    ) -> SessionHandle:
        """Create a new session."""
        json_data = self._dump(request) if request else None
        data = await self._post("/session", json=json_data)
        return SessionHandle(self, Session.model_validate(data))

    async def session(self, session_id: str) -> SessionHandle:
        """Get a session handle by ID."""
        data = await self._get(f"/session/{session_id}")
        return SessionHandle(self, Session.model_validate(data))

    async def update_session(
        self,
        session_id: str,
        request: SessionUpdateRequest,
    ) -> Session:
        """Update a session (e.g. title, archive)."""
        data = await self._patch(f"/session/{session_id}", json=self._dump(request))
        return Session.model_validate(data)

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        data = await self._delete(f"/session/{session_id}")
        return bool(data)

    async def get_session_status(self) -> dict[str, SessionStatus]:
        """Get status for all sessions (only non-idle returned)."""
        data = await self._get("/session/status")
        return {k: SessionStatus.model_validate(v) for k, v in data.items()}

    async def get_session_children(self, session_id: str) -> list[Session]:
        """Get child sessions."""
        data = await self._get(f"/session/{session_id}/children")
        return [Session.model_validate(s) for s in data]

    async def abort_session(self, session_id: str) -> bool:
        """Abort a busy session."""
        data = await self._post(f"/session/{session_id}/abort")
        return bool(data)

    async def fork_session(
        self,
        session_id: str,
        request: SessionForkRequest | None = None,
    ) -> SessionHandle:
        """Fork a session, optionally from a specific message."""
        json_data = self._dump(request) if request else None
        data = await self._post(f"/session/{session_id}/fork", json=json_data)
        return SessionHandle(self, Session.model_validate(data))

    async def init_session(
        self,
        session_id: str,
        request: SessionInitRequest | None = None,
    ) -> MessageWithParts:
        """Initialize a session (create AGENTS.md)."""
        json_data = self._dump(request) if request else None
        data = await self._post(f"/session/{session_id}/init", json=json_data)
        return MessageWithParts.model_validate(data)

    async def get_session_todos(self, session_id: str) -> list[Todo]:
        """Get todos for a session."""
        data = await self._get(f"/session/{session_id}/todo")
        return [Todo.model_validate(t) for t in data]

    async def get_session_diff(self, session_id: str) -> list[FileDiff]:
        """Get file diffs for a session."""
        data = await self._get(f"/session/{session_id}/diff")
        return [FileDiff.model_validate(d) for d in data]

    async def shell(
        self,
        session_id: str,
        request: ShellRequest,
    ) -> MessageWithParts:
        """Run a shell command in a session."""
        data = await self._post(f"/session/{session_id}/shell", json=self._dump(request))
        return MessageWithParts.model_validate(data)

    async def summarize(
        self,
        session_id: str,
        request: SummarizeRequest | None = None,
    ) -> MessageWithParts:
        """Summarize/compact a session."""
        json_data = self._dump(request) if request else None
        data = await self._post(f"/session/{session_id}/summarize", json=json_data)
        return MessageWithParts.model_validate(data)

    async def share_session(self, session_id: str) -> Session:
        """Share a session (create shareable link)."""
        data = await self._post(f"/session/{session_id}/share")
        return Session.model_validate(data)

    async def unshare_session(self, session_id: str) -> Session:
        """Remove session sharing."""
        data = await self._delete(f"/session/{session_id}/share")
        return Session.model_validate(data)

    async def revert_session(
        self,
        session_id: str,
        *,
        message_id: str,
        part_id: str | None = None,
    ) -> Session:
        """Revert a session to a specific message."""
        body: dict[str, str | None] = {"message_id": message_id, "part_id": part_id}
        data = await self._post(f"/session/{session_id}/revert", json=body)
        return Session.model_validate(data)

    async def unrevert_session(self, session_id: str) -> Session:
        """Undo a revert."""
        data = await self._post(f"/session/{session_id}/unrevert")
        return Session.model_validate(data)

    async def execute_command(
        self,
        session_id: str,
        request: CommandRequest,
    ) -> MessageWithParts:
        """Execute a slash command in a session."""
        data = await self._post(f"/session/{session_id}/command", json=self._dump(request))
        return MessageWithParts.model_validate(data)

    # ── Messages ──────────────────────────────────────────────────────

    async def list_messages(
        self,
        session_id: str,
        *,
        limit: int | None = None,
    ) -> list[MessageWithParts]:
        """List messages in a session."""
        data = await self._get(f"/session/{session_id}/message", limit=limit)
        return [MessageWithParts.model_validate(m) for m in data]

    async def send_message(
        self,
        session_id: str,
        request: MessageRequest,
    ) -> MessageWithParts:
        """Send a message and wait for the agent's response."""
        data = await self._post(f"/session/{session_id}/message", json=self._dump(request))
        return MessageWithParts.model_validate(data)

    async def send_message_async(self, session_id: str, request: MessageRequest) -> None:
        """Send a message asynchronously (returns immediately, listen to SSE for updates)."""
        await self._post(f"/session/{session_id}/prompt_async", json=self._dump(request))

    async def get_message(self, session_id: str, message_id: str) -> MessageWithParts:
        """Get a specific message."""
        data = await self._get(f"/session/{session_id}/message/{message_id}")
        return MessageWithParts.model_validate(data)

    async def delete_message(self, session_id: str, message_id: str) -> bool:
        """Delete a message and all its parts."""
        data = await self._delete(f"/session/{session_id}/message/{message_id}")
        return bool(data)

    # ── Permissions ───────────────────────────────────────────────────

    async def list_permissions(self, session_id: str) -> list[PermissionAskedProperties]:
        """Get pending permission requests for a session."""
        data = await self._get(f"/session/{session_id}/permissions")
        return [PermissionAskedProperties.model_validate(p) for p in data]

    async def reply_permission(
        self,
        session_id: str,
        permission_id: str,
        reply: PermissionReplyRequest,
    ) -> bool:
        """Reply to a permission request."""
        data = await self._post(
            f"/session/{session_id}/permissions/{permission_id}",
            json=self._dump(reply),
        )
        return bool(data)

    # ── Questions ─────────────────────────────────────────────────────

    async def list_questions(self) -> list[QuestionRequest]:
        """Get pending question requests."""
        data = await self._get("/question/")
        return [QuestionRequest.model_validate(q) for q in data]

    async def reply_question(
        self,
        request_id: str,
        reply: QuestionReply,
    ) -> bool:
        """Reply to a question request."""
        data = await self._post(f"/question/{request_id}/reply", json=self._dump(reply))
        return bool(data)

    async def reject_question(self, request_id: str) -> bool:
        """Reject a question request."""
        data = await self._post(f"/question/{request_id}/reject")
        return bool(data)

    # ── Agent / Skills / Commands ─────────────────────────────────────

    async def list_agents(self) -> list[Agent]:
        """List available agents."""
        from opencode_sdk.models.agent import Agent

        data = await self._get("/agent")
        return [Agent.model_validate(a) for a in data]

    async def list_skills(self) -> list[SkillInfo]:
        """List available skills/tools."""
        from opencode_sdk.models.agent import SkillInfo

        data = await self._get("/skill")
        return [SkillInfo.model_validate(s) for s in data]

    async def list_commands(self) -> list[Command]:
        """List available slash commands."""
        from opencode_sdk.models.agent import Command

        data = await self._get("/command")
        return [Command.model_validate(c) for c in data]

    # ── MCP ───────────────────────────────────────────────────────────

    async def list_mcp_servers(self) -> list[MCPStatus]:
        """List MCP server statuses."""
        data = await self._get("/mcp")
        return [MCPStatus.model_validate(s) for s in data]

    async def add_mcp_server(self, request: AddMcpServerRequest) -> MCPStatus:
        """Add an MCP server dynamically."""
        data = await self._post("/mcp", json=self._dump(request))
        return MCPStatus.model_validate(data)

    async def connect_mcp_server(self, name: str) -> MCPStatus:
        """Connect/reconnect an MCP server."""
        data = await self._post(f"/mcp/{name}/connect")
        return MCPStatus.model_validate(data)

    async def disconnect_mcp_server(self, name: str) -> MCPStatus:
        """Disconnect an MCP server."""
        data = await self._post(f"/mcp/{name}/disconnect")
        return MCPStatus.model_validate(data)

    # ── Logging ───────────────────────────────────────────────────────

    async def log(
        self,
        message: str,
        *,
        service: str = "opencode-client",
        level: LogLevel = "info",
    ) -> None:
        """Send a log message to the server."""
        from opencode_sdk.models.mcp import LogRequest

        req = LogRequest(service=service, level=level, message=message)
        await self._post("/log", json=self._dump(req))
