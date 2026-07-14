"""Memory capability — persistent memory across turns.

Stores and retrieves key-value memories via ``after_node_run`` (persist)
and ``before_model_request`` (inject). Memories are scoped per session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest


if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.capabilities import AgentNode, NodeResult
    from pydantic_ai.models import ModelRequestContext


@dataclass
class MemoryCapability(AbstractCapability[Any]):
    """Persist and retrieve memory across conversation turns.

    After each node run, extracts memories from the conversation result
    and stores them. Before each model request, injects relevant memories
    into the system prompt so the model has context from prior turns.

    Memory extraction and injection are delegated to callables so
    different strategies (LLM-based extraction, keyword matching,
    vector search) can be plugged in.

    The store is **shared** across all per-run copies (``for_run()`` does
    not copy the dict) so that memories extracted during a run persist
    into subsequent runs.
    """

    _store: dict[str, str] = field(default_factory=dict, repr=False)
    _extract_fn: Any = field(default=None, repr=False)
    _inject_fn: Any = field(default=None, repr=False)

    @property
    def has_wrap_node_run(self) -> bool:
        return False

    def set_extract_fn(self, fn: Any) -> None:
        self._extract_fn = fn

    def set_inject_fn(self, fn: Any) -> None:
        self._inject_fn = fn

    async def after_node_run(
        self,
        ctx: RunContext[Any],
        *,
        node: AgentNode[Any],
        result: NodeResult[Any],
    ) -> NodeResult[Any]:
        if self._extract_fn is None:
            return result
        new_memories: dict[str, str] = await self._extract_fn(result)
        if new_memories:
            self._store.update(new_memories)
        return result

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        if not self._store or self._inject_fn is None:
            return request_context
        injected = await self._inject_fn(self._store, request_context.messages)
        if not injected:
            return request_context
        messages: list[ModelMessage] = request_context.messages
        _inject_into_system_prompt(messages, injected)
        return request_context

    async def for_run(self, ctx: RunContext[Any]) -> MemoryCapability:
        cap = MemoryCapability()
        # Share the same dict reference so memories persist across runs.
        cap._store = self._store
        cap._extract_fn = self._extract_fn
        cap._inject_fn = self._inject_fn
        return cap


def _inject_into_system_prompt(messages: list[ModelMessage], injected: str) -> bool:
    """Append ``injected`` text to the first ``SystemPromptPart`` found.

    Pydantic AI stores system prompts as ``SystemPromptPart`` objects
    inside ``ModelRequest.parts``, not as a ``system_prompt`` attribute
    on the message itself. This helper iterates parts to find and update
    the correct one.

    Returns ``True`` if injection was applied, ``False`` otherwise.
    """
    for msg in messages:
        if not _is_model_request(msg):
            continue
        for part in msg.parts:
            if part.part_kind == "system-prompt":
                if injected not in part.content:
                    part.content = f"{part.content}\n\n{injected}"
                return True
    return False


def _is_model_request(msg: ModelMessage) -> bool:
    """Type-narrow ``ModelMessage`` to ``ModelRequest`` with kind check."""
    return msg.kind == "request" and isinstance(msg, ModelRequest)
