"""Dynamic context capability — manages context window expansion.

Applies compaction via ``before_model_request`` when the conversation
history approaches the model's context limit. Prevents context window
overflow by summarizing older messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability

from agentpool.log import get_logger


if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import ModelRequestContext


logger = get_logger(__name__)


@dataclass
class DynamicContextCapability(AbstractCapability[Any]):
    """Manage context window by compacting older messages.

    Before each model request, checks if the conversation history exceeds
    ``compaction_threshold`` messages. If so, older messages are summarized
    to keep the context within bounds.

    The actual compaction strategy is delegated to a callable so different
    summarization approaches (LLM-based, truncation, sliding window) can
    be plugged in. If no compaction function is configured, a warning is
    logged so users know the capability is not actively compacting.
    """

    max_messages: int = 50
    compaction_threshold: float = 0.8
    _compaction_fn: Any = field(default=None, repr=False)

    _MIN_MESSAGES: int = 2

    def __post_init__(self) -> None:
        if self.max_messages < self._MIN_MESSAGES:
            msg = f"max_messages must be >= {self._MIN_MESSAGES}, got {self.max_messages}"
            raise ValueError(msg)
        if not 0.0 < self.compaction_threshold <= 1.0:
            msg = f"compaction_threshold must be in (0, 1], got {self.compaction_threshold}"
            raise ValueError(msg)

    @property
    def has_wrap_node_run(self) -> bool:
        return False

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        messages: list[ModelMessage] = request_context.messages
        threshold_count = int(self.max_messages * self.compaction_threshold)
        if len(messages) <= threshold_count:
            return request_context
        if self._compaction_fn is not None:
            compacted = await self._compaction_fn(messages, threshold_count)
            request_context.messages = compacted
        else:
            logger.warning(
                "DynamicContextCapability: message count (%d) exceeded threshold (%d), "
                "but no compaction function is configured.",
                len(messages),
                threshold_count,
            )
        return request_context

    def set_compaction_fn(self, fn: Any) -> None:
        self._compaction_fn = fn

    async def for_run(self, ctx: RunContext[Any]) -> DynamicContextCapability:
        cap = DynamicContextCapability(
            max_messages=self.max_messages,
            compaction_threshold=self.compaction_threshold,
        )
        cap._compaction_fn = self._compaction_fn
        return cap
