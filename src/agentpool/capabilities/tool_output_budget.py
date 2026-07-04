"""Tool output budget capability — limits tool output size.

Truncates tool results via ``wrap_tool_execute`` when they exceed
``max_output_chars``, appending a truncation notice.

Relationship with _ToolInterceptCapability
===========================================

``_ToolInterceptCapability`` (``hook_manager.py``) owns ALL tool interception:
pre-tool hooks, post-tool hooks, error handling, and injection.

``ToolOutputBudgetCapability`` implements ``wrap_tool_execute`` to truncate
tool outputs that exceed the budget.

When both are in the capability chain, ``ToolOutputBudgetCapability``'s
``wrap_tool_execute`` runs AFTER ``_ToolInterceptCapability``'s. This is
because ``CombinedCapability`` chains ``wrap_tool_execute`` in **reverse**
order — the last capability in the list wraps the outermost. The injection
order in ``get_agentlet()`` places:

1. ``_ToolInterceptCapability`` first (innermost — error handling + hooks)
2. ``ToolOutputBudgetCapability`` last (outermost — budget truncation)

This means:
1. ``_ToolInterceptCapability`` wraps the tool first (innermost) — handles
   errors, runs hooks, applies modifications.
2. ``ToolOutputBudgetCapability`` wraps the result (outermost) — truncates
   if over budget.

This ordering is **correct**: budget truncation should happen last, after
all post-tool hooks have had a chance to modify the output. If truncation
ran first, hooks would see an artificially shortened output.

No code change is needed — the ordering is already correct by virtue of
capability injection order in ``get_agentlet()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability


if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.capabilities import WrapToolExecuteHandler
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition


_TRUNCATION_NOTICE = "\n[Tool output truncated by ToolOutputBudgetCapability]"


@dataclass
class ToolOutputBudgetCapability(AbstractCapability[Any]):
    """Limit tool output size per tool call.

    Wraps ``tool_execute`` and truncates string results that exceed
    ``max_output_chars``. A truncation notice is appended so the model
    knows the output was cut.
    """

    max_output_chars: int = 10_000

    _MIN_OUTPUT_CHARS = 100

    def __post_init__(self) -> None:
        if self.max_output_chars < self._MIN_OUTPUT_CHARS:
            msg = (
                f"max_output_chars must be >= {self._MIN_OUTPUT_CHARS}, got {self.max_output_chars}"
            )
            raise ValueError(msg)

    @property
    def has_wrap_node_run(self) -> bool:
        return False

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        result = await handler(args)
        match result:
            case str():
                return self._truncate(result)
            case list():
                return [self._truncate(item) if isinstance(item, str) else item for item in result]
            case _:
                pass
        return result

    def _truncate(self, text: str) -> str:
        if len(text) > self.max_output_chars:
            return text[: self.max_output_chars] + _TRUNCATION_NOTICE
        return text

    async def for_run(self, ctx: RunContext[Any]) -> ToolOutputBudgetCapability:
        return ToolOutputBudgetCapability(max_output_chars=self.max_output_chars)
