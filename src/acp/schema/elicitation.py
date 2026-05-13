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

    requested_schema: dict[str, Any] = Field(alias="requestedSchema")
    """A JSON Schema object describing the expected input structure."""

    url: str | None = None
    """Optional URL for URL-based elicitation (e.g., OAuth flows).

    When present, the client should open this URL for the user to complete
    the elicitation externally, then signal completion via notification.
    """


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

    Sent by the client when the user has completed an external elicitation
    flow (e.g., finished OAuth in the browser). This is a fire-and-forget
    notification - the agent does not wait for or expect a response.

    See protocol docs: [Elicitation](https://agentclientprotocol.com/protocol/elicitation)
    """

    session_id: str
    """The session ID this elicitation belongs to."""

    action: Literal["accept", "decline", "cancel"]
    """The user's decision after completing the external elicitation."""

    content: dict[str, Any] | None = None
    """The structured content resulting from the elicitation.

    Only present when action is 'accept'.
    """


class URLElicitationRequiredError(Schema):
    """Error indicating that URL-based elicitation is required.

    Returned when the agent requests elicitation but the client does not
    support the `elicitation/create` method. The client can use the
    provided URL to complete the elicitation externally.
    """

    url: str
    """The URL the user should visit to complete the elicitation."""

    message: str
    """A human-readable message explaining what the user needs to do."""
