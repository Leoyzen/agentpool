"""Common schema definitions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import Discriminator, Field, Tag

from acp.schema.base import AnnotatedObject, Schema


class EnvVariable(AnnotatedObject):
    """An environment variable to set when launching an MCP server."""

    name: str
    """The name of the environment variable."""

    value: str
    """The value to set for the environment variable."""


class Implementation(Schema):
    """Describes the name and version of an ACP implementation.

    Includes an optional title for UI representation.
    """

    name: str
    """Intended for programmatic or logical use.

    Can be used as a display name fallback if title isn't present."""

    title: str | None = None
    """Intended for UI and end-user contexts.

    Optimized to be human-readable and easily understood.
    If not provided, the name should be used for display."""

    version: str
    """Version of the implementation.

    Can be displayed to the user or used for debugging or metrics purposes."""


class AuthMethodAgent(AnnotatedObject):
    """Agent handles authentication itself.

    This is the default authentication method type.
    When no ``type`` field is present, the method is treated as ``agent``.
    """

    type: Literal["agent"] = Field(default="agent", exclude=True)
    """Discriminator field.  Defaults to ``"agent"`` and is excluded from serialization
    for backward compatibility."""

    id: str
    """Unique identifier for this authentication method."""

    name: str
    """Human-readable name of the authentication method."""

    description: str | None = None
    """Optional description providing more details about this authentication method."""


class AuthEnvVar(AnnotatedObject):
    """**UNSTABLE**: Describes a single environment variable for an env-var authentication method."""

    name: str
    """The environment variable name (e.g. ``"OPENAI_API_KEY"``)."""

    label: str | None = None
    """Human-readable label for this variable, displayed in client UI."""

    secret: bool = True
    """Whether this value is a secret (e.g. API key, token).

    Clients should use a password-style input for secret vars.
    Defaults to ``True``."""

    optional: bool = False
    """Whether this variable is optional.  Defaults to ``False``."""


class AuthMethodEnvVar(AnnotatedObject):
    """**UNSTABLE**: Environment variable authentication method.

    The user provides credentials that the client passes to the agent
    as environment variables.
    """

    type: Literal["env_var"] = "env_var"
    """Discriminator field."""

    id: str
    """Unique identifier for this authentication method."""

    name: str
    """Human-readable name of the authentication method."""

    description: str | None = None
    """Optional description providing more details about this authentication method."""

    vars: Sequence[AuthEnvVar]
    """The environment variables the client should set."""

    link: str | None = None
    """Optional link to a page where the user can obtain their credentials."""


class AuthMethodTerminal(AnnotatedObject):
    """**UNSTABLE**: Terminal authentication method.

    Client runs an interactive terminal for the user to authenticate via a TUI.
    """

    type: Literal["terminal"] = "terminal"
    """Discriminator field."""

    id: str
    """Unique identifier for this authentication method."""

    name: str
    """Human-readable name of the authentication method."""

    description: str | None = None
    """Optional description providing more details about this authentication method."""

    args: Sequence[str] = ()
    """Additional arguments to pass when running the agent binary for terminal auth."""

    env: dict[str, str] = Field(default_factory=dict)
    """Additional environment variables to set when running the agent binary for terminal auth."""


def _auth_method_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        return v.get("type", "agent")
    return getattr(v, "type", "agent")


AuthMethod = Annotated[
    Annotated[AuthMethodAgent, Tag("agent")]
    | Annotated[AuthMethodEnvVar, Tag("env_var")]
    | Annotated[AuthMethodTerminal, Tag("terminal")],
    Discriminator(_auth_method_discriminator),
]
"""Describes an available authentication method.

The ``type`` field acts as the discriminator.  When no ``type`` is present,
the method is treated as ``agent``.
"""


class Error(Schema):
    """JSON-RPC error object.

    Represents an error that occurred during method execution, following the
    JSON-RPC 2.0 error object specification with optional additional data.

    See protocol docs: [JSON-RPC Error Object](https://www.jsonrpc.org/specification#error_object)
    """

    code: int
    """A number indicating the error type that occurred.

    This must be an integer as defined in the JSON-RPC specification.
    """

    data: Any | None = None
    """Optional primitive or structured value that contains additional errorinformation.

    This may include debugging information or context-specific details.
    """

    message: str
    """A string providing a short description of the error.

    The message should be limited to a concise single sentence.
    """

    auth_methods: Sequence[AuthMethod] = ()
    """**UNSTABLE**: Authentication methods relevant to this error.

    Typically included with ``AUTH_REQUIRED`` errors to narrow down which
    authentication methods are applicable from those shared during initialization.
    """
