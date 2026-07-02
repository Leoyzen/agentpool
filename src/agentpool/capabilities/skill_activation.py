"""Skill activation capability — dynamic per-turn skill injection.

Supersedes ``SkillBridgeCapability`` (Phase 5 interim). Uses
``before_model_request`` to dynamically select and inject relevant
skill instructions based on the current conversation context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability


if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.messages import ModelRequestContext


@dataclass
class SkillActivationCapability(AbstractCapability[Any]):
    """Dynamically activate skills based on conversation context.

    Before each model request, evaluates which skills are relevant to
    the current prompt and injects their instructions into the system
    prompt. This replaces the static injection approach used by
    ``SkillsInstructionProvider``.

    Skill matching is delegated to a callable that receives the
    conversation messages and returns a list of skill names to activate.
    """

    _skills: dict[str, str] = field(default_factory=dict, repr=False)
    _matcher_fn: Any = field(default=None, repr=False)

    @property
    def has_wrap_node_run(self) -> bool:
        return False

    def register_skill(self, name: str, instructions: str) -> None:
        self._skills[name] = instructions

    def set_matcher(self, fn: Any) -> None:
        self._matcher_fn = fn

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        if not self._skills or self._matcher_fn is None:
            return request_context
        messages = request_context.messages
        active_names: list[str] = []
        if self._matcher_fn is not None:
            result = await self._matcher_fn(messages, list(self._skills.keys()))
            active_names = [n for n in result if n in self._skills]
        if not active_names:
            return request_context
        injected = "\n\n".join(
            f'<skill name="{name}">\n{self._skills[name]}\n</skill>'
            for name in active_names
        )
        for msg in messages:
            system_prompt = getattr(msg, "system_prompt", None)
            if system_prompt is not None and injected not in system_prompt:
                msg.system_prompt = f"{system_prompt}\n\n{injected}"
                break
        return request_context

    def for_run(self, ctx: RunContext[Any]) -> SkillActivationCapability:
        cap = SkillActivationCapability()
        cap._skills = dict(self._skills)
        cap._matcher_fn = self._matcher_fn
        return cap
