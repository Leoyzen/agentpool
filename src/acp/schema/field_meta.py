"""Undocumented ``_meta`` field conventions used by ACP implementations.

The ACP protocol includes a generic ``_meta`` (``field_meta``) extension point on
most schema objects (via :class:`~acp.schema.base.AnnotatedObject`). Several
implementations — notably ``claude-agent-acp``, ``codex-acp``, and Zed — have
established conventions for what goes into these fields. None of these are part
of the official ACP specification.

This module provides typed dictionaries documenting every known convention so
that implementors can produce and consume them with type safety rather than
relying on raw ``dict[str, Any]`` access.

Sources:
    - claude-agent-acp: ``src/acp-agent.ts`` (ToolUpdateMeta, NewSessionMeta, GatewayAuthMeta)
    - Zed: ``crates/agent_servers/src/acp.rs`` (terminal meta consumption, terminal-auth)
    - Zed: ``crates/acp_thread/src/acp_thread.rs`` (tool_name, subagent_session_info)
"""

from __future__ import annotations

from typing import Any, TypedDict


# ---------------------------------------------------------------------------
# Tool call / tool_call_update meta  (_meta on ToolCallStart / ToolCallProgress)
# ---------------------------------------------------------------------------


class ClaudeCodeToolMeta(TypedDict, total=False):
    """Claude Code-specific metadata attached to tool call updates.

    Produced by ``claude-agent-acp`` in ``streamEventToAcpNotifications()``
    (``src/acp-agent.ts:1760-1890``).

    Consumed by Zed indirectly — Zed currently reads ``tool_name`` from the
    top-level meta, not from this nested object.
    """

    toolName: str
    """The name of the tool as known to Claude Code (e.g. ``Bash``, ``Edit``, ``Read``).

    Source: ``claude-agent-acp/src/acp-agent.ts:178``
    """

    toolResponse: Any
    """Structured output from the tool execution.

    For the ``Edit`` tool this contains the full structured patch with
    ``filePath`` and ``structuredPatch`` fields, processed by
    ``toolUpdateFromEditToolResponse()`` (``claude-agent-acp/src/tools.ts:698``).

    Source: ``claude-agent-acp/src/acp-agent.ts:180``
    """

    parentToolUseId: str
    """When a tool call is made inside a sub-agent (e.g. via the ``Agent`` / ``Task``
    tool), this field links back to the parent tool use that spawned the sub-agent.

    Set in ``toAcpNotifications()`` when ``options.parentToolUseId`` is provided
    (``claude-agent-acp/src/acp-agent.ts:1696-1701``).

    Not yet consumed by Zed as of the current codebase.

    Source: ``claude-agent-acp/src/acp-agent.ts:1681``
    """


class TerminalInfoMeta(TypedDict):
    """Metadata to request creation of a display-only terminal in the client.

    Sent on the initial ``tool_call`` session update for ``Bash`` tools when
    the client advertises ``terminal_output`` support in its capabilities meta.

    The client should create a display-only terminal (no real PTY) identified
    by ``terminal_id`` to render streaming output.

    Producer: ``claude-agent-acp/src/acp-agent.ts:1821-1823``
    Consumer: ``zed/crates/agent_servers/src/acp.rs:1244-1272``
        Creates a ``TerminalBuilder::new_display_only`` and registers it via
        ``TerminalProviderEvent::Created``.
    """

    terminal_id: str
    """Unique identifier for the terminal session.

    Typically reuses the Claude Code tool use ID (``chunk.id``).
    """


class TerminalOutputMeta(TypedDict):
    """Metadata carrying terminal output data for a display-only terminal.

    Sent as a separate ``tool_call_update`` notification between the initial
    ``tool_call`` (which creates the terminal) and the final ``tool_call_update``
    (which carries the exit status).

    Producer: ``claude-agent-acp/src/acp-agent.ts:1860-1875``
              ``claude-agent-acp/src/tools.ts:505-510``
    Consumer: ``zed/crates/agent_servers/src/acp.rs:1290-1302``
        Feeds data into the terminal via ``TerminalProviderEvent::Output``.
    """

    terminal_id: str
    """The terminal to write output to. Must match a previously created terminal."""

    data: str
    """Raw terminal output as a string (stdout/stderr combined)."""


class TerminalExitMeta(TypedDict):
    """Metadata signaling that a terminal process has exited.

    Sent on the final ``tool_call_update`` for Bash tools alongside the
    ``completed`` / ``failed`` status.

    Producer: ``claude-agent-acp/src/acp-agent.ts:1883-1886``
              ``claude-agent-acp/src/tools.ts:511-515``
    Consumer: ``zed/crates/agent_servers/src/acp.rs:1306-1330``
        Signals exit via ``TerminalProviderEvent::Exit`` with exit code and signal.
    """

    terminal_id: str
    """The terminal that exited. Must match a previously created terminal."""

    exit_code: int
    """Process exit code (0 = success)."""

    signal: str | None
    """Signal that terminated the process, or ``None`` if exited normally."""


class ToolUpdateMeta(TypedDict, total=False):
    """Complete ``_meta`` shape for ``tool_call`` and ``tool_call_update`` session updates.

    This is the top-level ``_meta`` object attached to
    :class:`~acp.schema.session_updates.ToolCallStart` and
    :class:`~acp.schema.session_updates.ToolCallProgress` notifications.

    Originally defined as ``ToolUpdateMeta`` in ``claude-agent-acp/src/acp-agent.ts:176``.
    The same terminal meta conventions are used by ``codex-acp`` — see the comment at
    ``claude-agent-acp/src/acp-agent.ts:183``:
    *"Terminal metadata for Bash tool execution, matching codex-acp's _meta protocol."*

    **Lifecycle for Bash tools with terminal support** (3 notifications):

    1. ``tool_call`` with ``terminal_info`` → client creates display-only terminal
    2. ``tool_call_update`` with ``terminal_output`` → client feeds output data
    3. ``tool_call_update`` with ``terminal_exit`` → client marks process exited

    This workaround exists because Claude Code and Codex execute bash commands
    server-side, but ACP normally expects the client to manage terminal processes
    via ``terminal/create``. The ``_meta`` fields enable terminal-like UI rendering
    in clients even though the process runs remotely.

    See: ``claude-agent-acp/src/acp-agent.ts:1855-1860`` (lifecycle comment)
    """

    claudeCode: ClaudeCodeToolMeta
    """Claude Code-specific tool metadata."""

    terminal_info: TerminalInfoMeta
    """Present on initial ``tool_call`` for Bash tools. Signals the client to
    create a display-only terminal widget."""

    terminal_output: TerminalOutputMeta
    """Present on ``tool_call_update`` to stream terminal output to the client."""

    terminal_exit: TerminalExitMeta
    """Present on final ``tool_call_update`` to signal process exit."""


# ---------------------------------------------------------------------------
# Tool call top-level meta  (meta on ToolCall / ToolCallStart, not _meta)
# ---------------------------------------------------------------------------
# These are conventions for the ACP-level ``meta`` field on ToolCall objects,
# separate from the ``_meta`` extension point.


class ToolCallMeta(TypedDict, total=False):
    """Conventions for the ``meta`` field on :class:`~acp.schema.tool_call.ToolCall`.

    These are used by Zed's own agents (not claude-agent-acp) to pass
    structured information alongside tool calls.

    Source: ``zed/crates/acp_thread/src/acp_thread.rs:39-75``
    """

    tool_name: str
    """The underlying tool name, used by Zed to label tool calls in the UI.

    Extracted via ``tool_name_from_meta()`` in
    ``zed/crates/acp_thread/src/acp_thread.rs:42-47``.
    Created via ``meta_with_tool_name()`` at line 50-51.
    """

    subagent_session_info: SubagentSessionInfoMeta
    """Metadata linking a tool call to a sub-agent session.

    Extracted via ``subagent_session_info_from_meta()`` in
    ``zed/crates/acp_thread/src/acp_thread.rs:69-72``.
    Set in ``zed/crates/agent/src/tools/spawn_agent_tool.rs:155-236``.
    """


class SubagentSessionInfoMeta(TypedDict):
    """Links a tool call to the sub-agent session it spawned.

    Stored as a JSON value under the ``subagent_session_info`` key in the
    tool call's ``meta`` field. Used by Zed to enable navigation into
    sub-agent conversation threads.

    Source: ``zed/crates/acp_thread/src/acp_thread.rs:57-66``
    """

    session_id: str
    """The session ID of the spawned sub-agent session."""

    message_start_index: int
    """Index of the first message in the sub-agent's turn."""

    message_end_index: int | None
    """Index of the last message returned by the sub-agent, or ``None``
    if the sub-agent has not yet completed."""


# ---------------------------------------------------------------------------
# Client capabilities meta  (_meta on ClientCapabilities)
# ---------------------------------------------------------------------------


class ClientCapabilitiesMeta(TypedDict, total=False):
    """Conventions for ``_meta`` on :class:`~acp.schema.capabilities.ClientCapabilities`.

    Sent during ``initialize`` to advertise non-standard client features.

    Source: ``zed/crates/agent_servers/src/acp.rs:286-289``
    Consumed by: ``claude-agent-acp/src/acp-agent.ts:973`` and ``:1686``
    """

    terminal_output: bool
    """When ``True``, the client supports rendering terminal output streamed
    via :class:`ToolUpdateMeta` ``terminal_info`` / ``terminal_output`` /
    ``terminal_exit`` fields.

    Without this, agents fall back to sending bash output as plain text
    content blocks.

    Set by Zed at ``crates/agent_servers/src/acp.rs:287``.
    Checked by claude-agent-acp at ``src/acp-agent.ts:973``:
    ``clientCapabilities?._meta?.["terminal_output"] === true``
    """


class TerminalAuthValue(TypedDict):
    """Value for the ``terminal-auth`` key in :class:`ClientCapabilitiesMeta`.

    When set to ``True`` (boolean), it signals the client supports terminal-based
    authentication. When set to an object (for Gemini workaround), it contains
    the command to spawn for authentication.

    Source: ``zed/crates/agent_servers/src/acp.rs:288`` (boolean ``true``)
    Source: ``zed/crates/agent_servers/src/acp.rs:325-335`` (Gemini workaround object)
    """

    label: str
    """Human-readable label for the auth command."""

    command: str
    """Path to the executable to run for authentication."""

    args: list[str]
    """Arguments to pass to the command."""

    env: dict[str, str]
    """Environment variables to set when running the command."""


# ---------------------------------------------------------------------------
# Auth capabilities meta  (_meta on AuthCapabilities)
# ---------------------------------------------------------------------------


class AuthCapabilitiesMeta(TypedDict, total=False):
    """Conventions for ``_meta`` on :class:`~acp.schema.capabilities.AuthCapabilities`.

    Checked by ``claude-agent-acp`` to decide whether to offer gateway auth.

    Source: ``claude-agent-acp/src/acp-agent.ts:285-286``
    """

    gateway: bool
    """When ``True``, the client supports the ``gateway`` authentication method,
    which redirects API calls through a custom base URL with injected headers.

    Checked at ``claude-agent-acp/src/acp-agent.ts:286``:
    ``request.clientCapabilities?.auth?._meta?.gateway === true``
    """


# ---------------------------------------------------------------------------
# Auth method meta  (_meta on AuthMethod)
# ---------------------------------------------------------------------------


class TerminalAuthMethodMeta(TypedDict):
    """Meta on an :class:`~acp.schema.common.AuthMethodTerminal` for terminal-based auth.

    Used by Zed as a workaround for agents (like Gemini) that need a CLI
    command spawned for authentication.

    Source: ``zed/crates/agent_servers/src/acp.rs:325-335``
    """

    terminal_auth: TerminalAuthValue  # note: wire format uses "terminal-auth"
    """Command specification for terminal-based authentication.

    Note: The wire format key is ``terminal-auth`` (with hyphen). The Python
    field name uses an underscore for identifier compatibility.
    """


# ---------------------------------------------------------------------------
# New session meta  (_meta on NewSessionRequest / session/new)
# ---------------------------------------------------------------------------


class ClaudeCodeSessionOptions(TypedDict, total=False):
    """Claude Code SDK options forwarded via session creation meta.

    These are passed through to the Claude Code SDK's ``Options`` type.
    Some parameters are managed by the ACP adapter and will be ignored
    if provided (``cwd``, ``permissionMode``, ``executable``, etc.).

    Source: ``claude-agent-acp/src/acp-agent.ts:136-156``
    """

    resume: bool
    """Whether to resume a previous Claude Code session."""

    hooks: dict[str, Any]
    """Hook definitions, merged with ACP's own hooks."""

    mcpServers: list[dict[str, Any]]
    """MCP server configurations, merged with ACP's own servers."""

    disallowedTools: list[str]
    """Tool names to disallow, merged with ACP's own disallowed tools."""

    tools: list[dict[str, Any]]
    """Tool definitions passed through to Claude Code.
    Defaults to the ``claude_code`` preset if not provided."""


class ClaudeCodeNewSessionMeta(TypedDict, total=False):
    """Claude Code-specific metadata nested under ``claudeCode``."""

    options: ClaudeCodeSessionOptions
    """Options forwarded to the Claude Code SDK."""


class NewSessionMeta(TypedDict, total=False):
    """``_meta`` shape for ``session/new`` requests.

    Allows clients to pass implementation-specific options when creating
    a new session with a Claude Code ACP agent.

    Source: ``claude-agent-acp/src/acp-agent.ts:136-156``
    Consumed at: ``claude-agent-acp/src/acp-agent.ts:368`` and ``:1205``
    """

    claudeCode: ClaudeCodeNewSessionMeta
    """Claude Code-specific session creation options."""


# ---------------------------------------------------------------------------
# Gateway authentication meta  (_meta on authenticate request)
# ---------------------------------------------------------------------------


class GatewayConfig(TypedDict):
    """Gateway configuration for routing API calls through a custom proxy.

    Source: ``claude-agent-acp/src/acp-agent.ts:160-170``
    """

    baseUrl: str
    """Base URL to redirect API calls to."""

    headers: dict[str, str]
    """Custom headers to inject into API requests."""


class GatewayAuthMeta(TypedDict):
    """``_meta`` shape for ``authenticate`` requests using the ``gateway`` method.

    When a client selects the ``gateway`` authentication method, it sends this
    metadata to configure API call routing through a custom endpoint. The agent
    maps these to environment variables that override the default Anthropic API
    configuration.

    Source: ``claude-agent-acp/src/acp-agent.ts:158-170``
    Consumed at: ``claude-agent-acp/src/acp-agent.ts:459``
    """

    gateway: GatewayConfig
    """Gateway routing configuration."""
