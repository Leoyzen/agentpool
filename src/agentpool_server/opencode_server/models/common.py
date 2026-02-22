"""Common/shared models used across multiple domains."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from pydantic import Field

from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.models.base import OpenCodeBaseModel


if TYPE_CHECKING:
    from pydantic_ai.usage import UsageBase


class TimeCreatedUpdated(OpenCodeBaseModel):
    """Timestamp with created and updated fields (milliseconds)."""

    created: int
    updated: int


class TimeCreated(OpenCodeBaseModel):
    """Timestamp with created field only (milliseconds)."""

    created: int

    @classmethod
    def now(cls) -> Self:
        return cls(created=now_ms())


class TimeStartEnd(OpenCodeBaseModel):
    """Timestamp with start and optional end (milliseconds)."""

    start: int
    end: int | None = None


class ModelRef(OpenCodeBaseModel):
    """Reference to a provider model (provider_id + model_id)."""

    provider_id: str
    model_id: str


class TokenCache(OpenCodeBaseModel):
    """Token cache information."""

    read: int = 0
    write: int = 0


class Tokens(OpenCodeBaseModel):
    """Token usage information."""

    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache: TokenCache = Field(default_factory=TokenCache)
    total: int | None = None

    @classmethod
    def from_pydantic_ai(cls, usage: UsageBase) -> Tokens:
        """Create from a pydantic-ai Usage object.

        Args:
            usage: pydantic-ai request usage with token counts.
        """
        reasoning = usage.details.get("reasoning_tokens", 0)
        return cls(
            input=usage.input_tokens,
            output=usage.output_tokens,
            reasoning=reasoning,
            cache=TokenCache(read=usage.cache_read_tokens, write=usage.cache_write_tokens),
            total=usage.total_tokens + reasoning,
        )


class TextSpan(OpenCodeBaseModel):
    """A text span in user input (value + start/end offsets)."""

    value: str
    start: int
    end: int


class FileDiff(OpenCodeBaseModel):
    """A file diff entry."""

    file: str
    before: str
    after: str
    additions: int
    deletions: int
