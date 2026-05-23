"""Elicitation schema definitions for ACP."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from acp.schema.base import AnnotatedObject, Request, Response, Schema


class ElicitationCreateRequest(Request):
    """Request to elicit input from the user.

    Sent when the agent needs structured input from the user that goes
    beyond simple permission grants. Supports both form-based and URL-based
    elicitation patterns.

    See protocol docs: [Elicitation](https://agentclientprotocol.com/protocol/elicitation)
    """

    session_id: str
    """The session ID for this request."""

    message: str
    """A human-readable message describing what input is being requested."""

    mode: Literal["form", "url"]
    """The elicitation mode: ``form`` for schema-based input, ``url`` for external URL flows."""

    requested_schema: dict[str, Any] = Field(alias="requestedSchema")
    """A JSON Schema object describing the expected input structure."""

    url: str | None = None
    """Optional URL for URL-based elicitation (e.g., OAuth flows).

    When present, the client should open this URL for the user to complete
    the elicitation externally, then signal completion via notification.
    """

    elicitation_id: str | None = Field(default=None, alias="elicitationId")
    """Unique identifier for this elicitation request. Required for URL mode."""


class ElicitationCreateResponse(Response):
    """Response to an elicitation request.

    Contains the user's decision and optionally the structured content
    they provided.

    See protocol docs: [Elicitation](https://agentclientprotocol.com/protocol/elicitation)
    """

    action: Literal["accept", "decline", "cancel"]
    """The user's decision on the elicitation request.

    - accept: User provided the requested input
    - decline: User declined to provide input
    - cancel: User cancelled the elicitation
    """

    content: dict[str, Any] | None = None
    """The structured content provided by the user.

    Only present when action is 'accept'. Must conform to the
    requested_schema from the original request.
    """


class ElicitationCompleteNotification(AnnotatedObject):
    """Notification signaling completion of a URL-based elicitation.

    Sent by the agent to inform the client that an out-of-band URL
    interaction has completed. The client MAY use this to automatically
    retry requests or update UI.

    See: https://agentclientprotocol.com/rfds/elicitation.md
    """

    session_id: str
    """The session ID this elicitation belongs to."""

    elicitation_id: str = Field(alias="elicitationId")
    """The ID of the elicitation that was completed."""


class ElicitationItem(Schema):
    """A single URL-mode elicitation requirement."""

    mode: Literal["url"] = "url"
    elicitation_id: str = Field(alias="elicitationId")
    url: str
    message: str


class URLElicitationRequiredError(Schema):
    """Error indicating that URL-based elicitation is required.

    Error code: -32042
    Returned when a request cannot be processed until a URL-mode
    elicitation is completed (e.g., OAuth authorization needed).
    """

    code: int = -32042
    """The JSON-RPC error code for URL elicitation required."""

    message: str
    """A human-readable error message."""

    elicitations: list[ElicitationItem] = Field(default_factory=list)
    """URL-mode elicitations that must be completed before retrying."""
