"""Capability schema."""

from __future__ import annotations

from typing import Any, Self

from pydantic import field_validator

from acp.schema.base import AnnotatedObject


class FileSystemCapability(AnnotatedObject):
    """File system capabilities that a client may support.

    See protocol docs: [FileSystem](https://agentclientprotocol.com/protocol/initialization#filesystem)
    """

    read_text_file: bool | None = False
    """Whether the Client supports `fs/read_text_file` requests."""

    write_text_file: bool | None = False
    """Whether the Client supports `fs/write_text_file` requests."""


class AuthCapabilities(AnnotatedObject):
    """**UNSTABLE**: Authentication capabilities supported by the client.

    Advertised during initialization to inform the agent which authentication
    method types the client can handle.
    """

    terminal: bool = False
    """Whether the client supports ``terminal`` authentication methods."""


class ElicitationCapabilities(AnnotatedObject):
    """Elicitation capabilities supported by the client.

    Advertised during initialization to inform the agent which
    elicitation modes the client supports.

    See protocol docs: [Elicitation](https://agentclientprotocol.com/protocol/elicitation)
    """

    form: bool | None = False
    """Whether the Client supports form-mode `elicitation/create` requests."""

    url: bool | None = False
    """Whether the Client supports URL-mode `elicitation/create` requests."""

    @field_validator("form", "url", mode="before")
    def _coerce_object_to_bool(cls, v: Any) -> bool | None:  # noqa: N805
        """Coerce ACP spec's {} or true to bool.

        The ACP spec represents capability presence as either an empty
        object ``{}`` or ``true``.  Both indicate the mode is supported.
        """
        if v is None:
            return None
        if isinstance(v, dict):
            return True
        return bool(v)

    def supports_elicitation(self) -> bool:
        """Check if any elicitation mode is supported."""
        return bool(self.form) or bool(self.url)


class ClientCapabilities(AnnotatedObject):
    """Capabilities supported by the client.

    Advertised during initialization to inform the agent about
    available features and methods.

    See protocol docs: [Client Capabilities](https://agentclientprotocol.com/protocol/initialization#client-capabilities)
    """

    auth: AuthCapabilities | None = None
    """**UNSTABLE**: Authentication capabilities supported by the client."""

    fs: FileSystemCapability | None = None
    """File system capabilities supported by the client.

    Determines which file operations the agent can request.
    """

    terminal: bool | None = False
    """Whether the Client support all `terminal/*` methods."""

    elicitation: ElicitationCapabilities | None = None
    """Elicitation capabilities supported by the client.

    Determines whether the agent can use `elicitation/create` for
    structured user input, or must fall back to `request_permission`.
    """

    @classmethod
    def create(
        cls,
        read_text_file: bool | None = False,
        write_text_file: bool | None = False,
        terminal: bool | None = False,
        auth: AuthCapabilities | None = None,
        elicitation: ElicitationCapabilities | None = None,
    ) -> Self:
        """Create a new instance of ClientCapabilities.

        Args:
            read_text_file: Whether the Client supports `fs/read_text_file` requests.
            write_text_file: Whether the Client supports `fs/write_text_file` requests.
            terminal: Whether the Client supports all `terminal/*` methods.
            auth: Authentication capabilities supported by the client.
            elicitation: Elicitation capabilities supported by the client.

        Returns:
            A new instance of ClientCapabilities.
        """
        fs = FileSystemCapability(read_text_file=read_text_file, write_text_file=write_text_file)
        return cls(fs=fs, terminal=terminal, auth=auth, elicitation=elicitation)


class PromptCapabilities(AnnotatedObject):
    """Prompt capabilities supported by the agent in `session/prompt` requests.

    Baseline agent functionality requires support for [`ContentBlock::Text`]
    and [`ContentBlock::ResourceContentBlock`] in prompt requests.

    Other variants must be explicitly opted in to.
    Capabilities for different types of content in prompt requests.

    Indicates which content types beyond the baseline (text and resource links)
    the agent can process.

    See protocol docs: [Prompt Capabilities](https://agentclientprotocol.com/protocol/initialization#prompt-capabilities)
    """

    audio: bool | None = False
    """Agent supports [`ContentBlock::Audio`]."""

    embedded_context: bool | None = False
    """Agent supports embedded context in `session/prompt` requests.

    When enabled, the Client is allowed to include [`ContentBlock::Resource`]
    in prompt requests for pieces of context that are referenced in the message.
    """

    image: bool | None = False
    """Agent supports [`ContentBlock::Image`]."""


class McpCapabilities(AnnotatedObject):
    """MCP capabilities supported by the agent."""

    http: bool | None = False
    """Agent supports [`McpServer::Http`]."""

    sse: bool | None = False
    """Agent supports [`McpServer::Sse`]."""


class SessionListCapabilities(AnnotatedObject):
    """Capabilities for the `session/list` method.

    **UNSTABLE**: This capability is not part of the spec yet,
    and may be removed or changed at any point.

    By supplying `{}` it means that the agent supports listing of sessions.
    Further capabilities can be added in the future for other means of
    filtering or searching the list.
    """


class SessionForkCapabilities(AnnotatedObject):
    """Capabilities for the `session/fork` method.

    **UNSTABLE**: This capability is not part of the spec yet,
    and may be removed or changed at any point.

    By supplying `{}` it means that the agent supports forking of sessions.
    """


class SessionResumeCapabilities(AnnotatedObject):
    """Capabilities for the `session/resume` method.

    **UNSTABLE**: This capability is not part of the spec yet,
    and may be removed or changed at any point.

    By supplying `{}` it means that the agent supports resuming of sessions.
    """


class SessionCloseCapabilities(AnnotatedObject):
    """Capabilities for the `session/close` method.

    **UNSTABLE**: This capability is not part of the spec yet,
    and may be removed or changed at any point.

    By supplying ``{}`` it means that the agent supports closing of sessions.
    """


class SessionCapabilities(AnnotatedObject):
    """Session capabilities supported by the agent.

    As a baseline, all Agents **MUST** support `session/new`, `session/prompt`,
    `session/cancel`, and `session/update`.

    Optionally, they **MAY** support other session methods and notifications
    by specifying additional capabilities.

    Note: `session/load` is still handled by the top-level `load_session` capability.
    This will be unified in future versions of the protocol.

    See protocol docs: [Session Capabilities](https://agentclientprotocol.com/protocol/initialization#session-capabilities)
    """

    close: SessionCloseCapabilities | None = None
    """**UNSTABLE**

    This capability is not part of the spec yet, and may be removed or changed at any point.

    Whether the agent supports `session/close`.
    """

    fork: SessionForkCapabilities | None = None
    """**UNSTABLE**

    This capability is not part of the spec yet, and may be removed or changed at any point.

    Whether the agent supports `session/fork`.
    """

    list: SessionListCapabilities | None = None
    """**UNSTABLE**

    This capability is not part of the spec yet, and may be removed or changed at any point.

    Whether the agent supports `session/list`.
    """

    resume: SessionResumeCapabilities | None = None
    """**UNSTABLE**

    This capability is not part of the spec yet, and may be removed or changed at any point.

    Whether the agent supports `session/resume`.
    """


class AgentCapabilities(AnnotatedObject):
    """Capabilities supported by the agent.

    Advertised during initialization to inform the client about
    available features and content types.

    See protocol docs: [Agent Capabilities](https://agentclientprotocol.com/protocol/initialization#agent-capabilities)
    """

    load_session: bool | None = False
    """Whether the agent supports `session/load`."""

    mcp_capabilities: McpCapabilities | None = None
    """MCP capabilities supported by the agent."""

    prompt_capabilities: PromptCapabilities | None = None
    """Prompt capabilities supported by the agent."""

    session_capabilities: SessionCapabilities | None = None
    """Session capabilities supported by the agent."""

    providers: bool | None = False
    """Whether the agent supports `providers/*` protocol methods.

    When enabled, the client can list, configure, and disable LLM providers
    via the providers/list, providers/set, and providers/disable methods.
    """

    @classmethod
    def create(
        cls,
        load_session: bool | None = False,
        http_mcp_servers: bool = False,
        sse_mcp_servers: bool = False,
        audio_prompts: bool = False,
        embedded_context_prompts: bool = False,
        image_prompts: bool = False,
        list_sessions: bool = False,
        resume_session: bool = False,
        close_session: bool = False,
        providers: bool = False,
    ) -> Self:
        """Create an instance of AgentCapabilities.

        Args:
            load_session: Whether the agent supports `session/load`.
            http_mcp_servers: Whether the agent supports HTTP MCP servers.
            sse_mcp_servers: Whether the agent supports SSE MCP servers.
            audio_prompts: Whether the agent supports audio prompts.
            embedded_context_prompts: Whether the agent supports embedded context prompts.
            image_prompts: Whether the agent supports image prompts.
            list_sessions: Whether the agent supports `session/list` (unstable).
            resume_session: Whether the agent supports `session/resume` (unstable).
            close_session: Whether the agent supports `session/close` (unstable).
            providers: Whether the agent supports `providers/*` methods.
        """
        session_caps = SessionCapabilities(
            list=SessionListCapabilities() if list_sessions else None,
            resume=SessionResumeCapabilities() if resume_session else None,
            close=SessionCloseCapabilities() if close_session else None,
        )
        return cls(
            load_session=load_session,
            providers=providers,
            mcp_capabilities=McpCapabilities(http=http_mcp_servers, sse=sse_mcp_servers),
            prompt_capabilities=PromptCapabilities(
                audio=audio_prompts,
                embedded_context=embedded_context_prompts,
                image=image_prompts,
            ),
            session_capabilities=session_caps,
        )
