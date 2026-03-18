"""Elicitation schema definitions.

**UNSTABLE**: This module is not part of the spec yet, and may be removed or changed at any point.

Defines types for agent-initiated elicitation, where the agent requests
structured input from the user via forms or URLs.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal, Self

from pydantic import Discriminator, Field, Tag

from acp.schema.base import AnnotatedObject, Request, Response


# --- String format ---


class StringFormat:
    """String format types for string properties in elicitation schemas."""

    EMAIL: Literal["email"] = "email"
    URI: Literal["uri"] = "uri"
    DATE: Literal["date"] = "date"
    DATE_TIME: Literal["date-time"] = "date-time"


StringFormatLiteral = Literal["email", "uri", "date", "date-time"]


# --- Enum option ---


class EnumOption(AnnotatedObject):
    """A titled enum option with a const value and human-readable title."""

    const: str
    """The constant value for this option."""

    title: str
    """Human-readable title for this option."""


# --- Property schemas ---


class StringPropertySchema(AnnotatedObject):
    """Schema for string properties in an elicitation form.

    When ``enum_values`` or ``one_of`` is set, this represents a single-select enum.
    """

    type: Literal["string"] = "string"
    """Type discriminator."""

    title: str | None = None
    """Optional title for the property."""

    description: str | None = None
    """Human-readable description."""

    min_length: int | None = Field(default=None, ge=0)
    """Minimum string length."""

    max_length: int | None = Field(default=None, ge=0)
    """Maximum string length."""

    pattern: str | None = None
    """Pattern the string must match."""

    format: StringFormatLiteral | None = None
    """String format."""

    default: str | None = None
    """Default value."""

    enum: Sequence[str] | None = None
    """Enum values for untitled single-select enums."""

    one_of: Sequence[EnumOption] | None = None
    """Titled enum options for titled single-select enums."""


class NumberPropertySchema(AnnotatedObject):
    """Schema for number (floating-point) properties in an elicitation form."""

    type: Literal["number"] = "number"
    """Type discriminator."""

    title: str | None = None
    """Optional title for the property."""

    description: str | None = None
    """Human-readable description."""

    minimum: float | None = None
    """Minimum value (inclusive)."""

    maximum: float | None = None
    """Maximum value (inclusive)."""

    default: float | None = None
    """Default value."""


class IntegerPropertySchema(AnnotatedObject):
    """Schema for integer properties in an elicitation form."""

    type: Literal["integer"] = "integer"
    """Type discriminator."""

    title: str | None = None
    """Optional title for the property."""

    description: str | None = None
    """Human-readable description."""

    minimum: int | None = None
    """Minimum value (inclusive)."""

    maximum: int | None = None
    """Maximum value (inclusive)."""

    default: int | None = None
    """Default value."""


class BooleanPropertySchema(AnnotatedObject):
    """Schema for boolean properties in an elicitation form."""

    type: Literal["boolean"] = "boolean"
    """Type discriminator."""

    title: str | None = None
    """Optional title for the property."""

    description: str | None = None
    """Human-readable description."""

    default: bool | None = None
    """Default value."""


# --- Multi-select items ---


class UntitledMultiSelectItems(AnnotatedObject):
    """Items definition for untitled multi-select enum properties."""

    type: Literal["string"] = "string"
    """Item type discriminator. Must be ``"string"``."""

    enum: Sequence[str]
    """Allowed enum values."""


class TitledMultiSelectItems(AnnotatedObject):
    """Items definition for titled multi-select enum properties."""

    any_of: Sequence[EnumOption]
    """Titled enum options."""


def _multi_select_items_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        if "anyOf" in v or "any_of" in v:
            return "titled"
        return "untitled"
    if isinstance(v, TitledMultiSelectItems):
        return "titled"
    return "untitled"


MultiSelectItems = Annotated[
    Annotated[UntitledMultiSelectItems, Tag("untitled")]
    | Annotated[TitledMultiSelectItems, Tag("titled")],
    Discriminator(_multi_select_items_discriminator),
]
"""Items for a multi-select (array) property schema."""


class MultiSelectPropertySchema(AnnotatedObject):
    """Schema for multi-select (array) properties in an elicitation form."""

    type: Literal["array"] = "array"
    """Type discriminator."""

    title: str | None = None
    """Optional title for the property."""

    description: str | None = None
    """Human-readable description."""

    min_items: int | None = Field(default=None, ge=0)
    """Minimum number of items to select."""

    max_items: int | None = Field(default=None, ge=0)
    """Maximum number of items to select."""

    items: MultiSelectItems
    """The items definition describing allowed values."""

    default: Sequence[str] | None = None
    """Default selected values."""


# --- Property schema union ---


def _property_schema_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        return v.get("type", "string")  # type: ignore[no-any-return]
    return v.type  # type: ignore[no-any-return]


ElicitationPropertySchema = Annotated[
    Annotated[StringPropertySchema, Tag("string")]
    | Annotated[NumberPropertySchema, Tag("number")]
    | Annotated[IntegerPropertySchema, Tag("integer")]
    | Annotated[BooleanPropertySchema, Tag("boolean")]
    | Annotated[MultiSelectPropertySchema, Tag("array")],
    Discriminator(_property_schema_discriminator),
]
"""Property schema for elicitation form fields.

Each variant corresponds to a JSON Schema ``"type"`` value.
"""


# --- Elicitation schema ---


class ElicitationSchema(AnnotatedObject):
    """Type-safe elicitation schema for requesting structured user input.

    This represents a JSON Schema object with primitive-typed properties,
    as required by the elicitation specification.
    """

    type: Literal["object"] = "object"
    """Type discriminator. Always ``"object"``."""

    title: str | None = None
    """Optional title for the schema."""

    description: str | None = None
    """Optional description of what this schema represents."""

    properties: dict[str, ElicitationPropertySchema] = Field(default_factory=dict)
    """Property definitions (must be primitive types)."""

    required: Sequence[str] | None = None
    """List of required property names."""


# --- Elicitation content value ---

ElicitationContentValue = str | int | float | bool | Sequence[str]
"""Possible value types in elicitation content."""


# --- Elicitation actions ---


class ElicitationAcceptAction(AnnotatedObject):
    """**UNSTABLE**: The user accepted the elicitation and provided content."""

    action: Literal["accept"] = "accept"
    """Discriminator value."""

    content: dict[str, ElicitationContentValue] | None = None
    """The user-provided content, if any, as an object matching the requested schema."""


class ElicitationDeclineAction(AnnotatedObject):
    """**UNSTABLE**: The user declined the elicitation."""

    action: Literal["decline"] = "decline"
    """Discriminator value."""


class ElicitationCancelAction(AnnotatedObject):
    """**UNSTABLE**: The elicitation was cancelled."""

    action: Literal["cancel"] = "cancel"
    """Discriminator value."""


def _elicitation_action_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        return v.get("action", "accept")  # type: ignore[no-any-return]
    return v.action  # type: ignore[no-any-return]


ElicitationAction = Annotated[
    Annotated[ElicitationAcceptAction, Tag("accept")]
    | Annotated[ElicitationDeclineAction, Tag("decline")]
    | Annotated[ElicitationCancelAction, Tag("cancel")],
    Discriminator(_elicitation_action_discriminator),
]
"""The user's action in response to an elicitation."""


# --- Elicitation capabilities ---


class ElicitationFormCapabilities(AnnotatedObject):
    """**UNSTABLE**: Form-based elicitation capabilities."""


class ElicitationUrlCapabilities(AnnotatedObject):
    """**UNSTABLE**: URL-based elicitation capabilities."""


class ElicitationCapabilities(AnnotatedObject):
    """**UNSTABLE**: Elicitation capabilities supported by the client."""

    form: ElicitationFormCapabilities | None = None
    """Whether the client supports form-based elicitation."""

    url: ElicitationUrlCapabilities | None = None
    """Whether the client supports URL-based elicitation."""


# --- Elicitation modes ---


class ElicitationFormMode(AnnotatedObject):
    """**UNSTABLE**: Form-based elicitation mode.

    The client renders a form from the provided schema.
    """

    mode: Literal["form"] = "form"
    """Discriminator value."""

    requested_schema: ElicitationSchema
    """A JSON Schema describing the form fields to present to the user."""


class ElicitationUrlMode(AnnotatedObject):
    """**UNSTABLE**: URL-based elicitation mode.

    The client directs the user to a URL.
    """

    mode: Literal["url"] = "url"
    """Discriminator value."""

    elicitation_id: str
    """The unique identifier for this elicitation."""

    url: str
    """The URL to direct the user to."""


def _elicitation_mode_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        return v.get("mode", "form")  # type: ignore[no-any-return]
    return v.mode  # type: ignore[no-any-return]


ElicitationMode = Annotated[
    Annotated[ElicitationFormMode, Tag("form")] | Annotated[ElicitationUrlMode, Tag("url")],
    Discriminator(_elicitation_mode_discriminator),
]
"""The mode of elicitation."""


# --- Elicitation request/response ---


class ElicitationRequest(Request):
    """**UNSTABLE**: Request from the agent to elicit structured user input.

    The agent sends this to the client to request information from the user,
    either via a form or by directing them to a URL.
    """

    session_id: str
    """The session ID for this request."""

    message: str
    """A human-readable message describing what input is needed."""

    mode: ElicitationMode
    """The elicitation mode and its mode-specific fields."""

    @classmethod
    def form(
        cls,
        session_id: str,
        message: str,
        schema: ElicitationSchema,
    ) -> Self:
        """Create a form-based elicitation request."""
        return cls(
            session_id=session_id,
            message=message,
            mode=ElicitationFormMode(requested_schema=schema),
        )

    @classmethod
    def url_based(
        cls,
        session_id: str,
        message: str,
        elicitation_id: str,
        url: str,
    ) -> Self:
        """Create a URL-based elicitation request."""
        return cls(
            session_id=session_id,
            message=message,
            mode=ElicitationUrlMode(elicitation_id=elicitation_id, url=url),
        )


class ElicitationResponse(Response):
    """**UNSTABLE**: Response from the client to an elicitation request."""

    action: ElicitationAction
    """The user's action in response to the elicitation."""


# --- Elicitation complete notification ---


class ElicitationCompleteNotification(AnnotatedObject):
    """**UNSTABLE**: Notification sent by the agent when a URL-based elicitation is complete."""

    elicitation_id: str
    """The ID of the elicitation that completed."""
