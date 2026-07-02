"""Loop detection capability — prevents infinite agent delegation loops.

Tracks delegation depth via ``wrap_node_run``. When depth exceeds
``max_depth``, raises ``LoopDetectionError`` to abort the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability


if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.agent import AgentNode, WrapNodeRunHandler
    from pydantic_ai.result import NodeResult


class LoopDetectionError(Exception):
    """Raised when delegation depth exceeds the configured maximum."""

    def __init__(self, depth: int, max_depth: int) -> None:
        self.depth = depth
        self.max_depth = max_depth
        super().__init__(
            f"Loop detection: delegation depth {depth} exceeds maximum {max_depth}. "
            f"This likely indicates an infinite agent delegation loop."
        )


@dataclass
class LoopDetectionCapability(AbstractCapability[Any]):
    """Prevent infinite agent loops via depth tracking.

    Wraps ``node_run`` and increments a depth counter on each nested
    call. When depth exceeds ``max_depth``, raises ``LoopDetectionError``.
    """

    max_depth: int = 10
    _depth: int = 0

    def __post_init__(self) -> None:
        if self.max_depth < 1:
            msg = f"max_depth must be >= 1, got {self.max_depth}"
            raise ValueError(msg)

    @property
    def has_wrap_node_run(self) -> bool:
        return True

    async def wrap_node_run(
        self,
        ctx: RunContext[Any],
        *,
        node: AgentNode[Any],
        handler: WrapNodeRunHandler[Any],
    ) -> NodeResult[Any]:
        self._depth += 1
        try:
            if self._depth > self.max_depth:
                raise LoopDetectionError(self._depth, self.max_depth)
            return await handler(ctx, node=node)
        finally:
            self._depth -= 1

    def for_run(self, ctx: RunContext[Any]) -> LoopDetectionCapability:
        return LoopDetectionCapability(max_depth=self.max_depth)
