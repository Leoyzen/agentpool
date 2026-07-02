"""Memory capability — persistent memory across turns.

Stores and retrieves key-value memories via ``after_node_run`` (persist)
and ``before_model_request`` (inject). Memories are scoped per session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability


if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.agent import AgentNode, NodeResult
    from pydantic_ai.messages import ModelMessage, ModelRequestContext


@dataclass
class MemoryCapability(AbstractCapability[Any]):
    """Persist and retrieve memory across conversation turns.

    After each node run, extracts memories from the conversation result
    and stores them. Before each model request, injects relevant memories
    into the system prompt so the model has context from prior turns.

    Memory extraction and injection are delegated to callables so
    different strategies (LLM-based extraction, keyword matching,
    vector search) can be plugged in.
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
        for msg in messages:
            system_prompt = getattr(msg, "system_prompt", None)
            if system_prompt is not None and injected not in system_prompt:
                msg.system_prompt = f"{system_prompt}\n\n{injected}"
                break
        return request_context

    def for_run(self, ctx: RunContext[Any]) -> MemoryCapability:
        cap = MemoryCapability()
        cap._store = dict(self._store)
        cap._extract_fn = self._extract_fn
        cap._inject_fn = self._inject_fn
        return cap
