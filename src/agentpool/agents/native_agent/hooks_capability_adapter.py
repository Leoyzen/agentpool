"""Adapter bridging AgentPool hooks to pydantic_ai.capabilities.Hooks.

Replaces the deprecated ``AgentHooks.as_capability()`` bridge with a
focused adapter that:

- Accepts hook callables organized by pydantic-ai callback name
- Implements deny > ask > allow priority combining inside callbacks
- Wraps CommandHook and PromptHook as thin callback adapters
- Produces a single ``pydantic_ai.capabilities.Hooks`` instance
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

from pydantic_ai.capabilities import Hooks

from agentpool.hooks.base import HookInput, HookResult
from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from exxec import ExecutionEnvironment
    from pydantic_ai import AgentRunResult
    from pydantic_ai.capabilities.abstract import ValidatedToolArgs
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import RunContext, ToolDefinition


logger = get_logger(__name__)

HookCallable: TypeAlias = "Callable[..., HookResult | None | Awaitable[HookResult | None]]"  # noqa: UP040

HookEventName: TypeAlias = Literal[  # noqa: UP040
    "before_run", "after_run", "before_tool_execute", "after_tool_execute"
]

class HooksCapabilityAdapter:
    """Builds a ``pydantic_ai.capabilities.Hooks`` from AgentPool hook callables.

    Each hook callable receives keyword arguments matching ``HookInput``
    fields and returns a ``HookResult`` (or ``None`` for implicit allow).

    The adapter handles:
    - Parallel execution of multiple hooks per callback
    - Priority combining: deny > ask > allow
    - Matcher filtering (regex on tool_name + input_match on tool_input fields)
    - Deny → ``RuntimeError`` (since pydantic-ai hooks can't return "block")
    - modified_input merging into validated tool args
    """

    def __init__(
        self,
        *,
        before_run: Sequence[HookCallable] | None = None,
        after_run: Sequence[HookCallable] | None = None,
        before_tool_execute: Sequence[HookCallable] | None = None,
        after_tool_execute: Sequence[HookCallable] | None = None,
        matchers: dict[HookEventName, Sequence[str | None]] | None = None,
        input_matchers: dict[HookEventName, Sequence[dict[str, str] | None]] | None = None,
    ) -> None:
        self._before_run = list(before_run) if before_run else []
        self._after_run = list(after_run) if after_run else []
        self._before_tool = list(before_tool_execute) if before_tool_execute else []
        self._after_tool = list(after_tool_execute) if after_tool_execute else []
        self._matchers = matchers or {}
        self._input_matchers = input_matchers or {}

    def build(self) -> Hooks:
        """Build and return a ``pydantic_ai.capabilities.Hooks`` instance."""
        kwargs: dict[str, Any] = {}

        if self._before_run:
            kwargs["before_run"] = self._wrap_before_run()
        if self._after_run:
            kwargs["after_run"] = self._wrap_after_run()
        if self._before_tool:
            kwargs["before_tool_execute"] = self._wrap_before_tool_execute()
        if self._after_tool:
            kwargs["after_tool_execute"] = self._wrap_after_tool_execute()

        return Hooks(**kwargs)

    # -----------------------------------------------------------------------
    # Priority combining — the core algorithm
    # -----------------------------------------------------------------------

    @staticmethod
    async def _combine(
        hooks: Sequence[HookCallable],
        input_data: HookInput,
        event: HookEventName,
        matchers: Sequence[str | None] | None = None,
        input_matchers: Sequence[dict[str, str] | None] | None = None,
        env: ExecutionEnvironment | None = None,
    ) -> HookResult:
        if not hooks:
            return HookResult(decision="allow")

        matching = [
            (i, h)
            for i, h in enumerate(hooks)
            if _matches(
                input_data,
                event,
                matchers[i] if matchers else None,
                input_matchers[i] if input_matchers else None,
            )
        ]
        if not matching:
            return HookResult(decision="allow")

        raw_results = await asyncio.gather(
            *(_execute_hook(h, input_data, env=env) for _, h in matching),
            return_exceptions=True,
        )

        combined = HookResult(decision="allow")
        reasons: list[str] = []
        contexts: list[str] = []

        for raw_result in raw_results:
            if isinstance(raw_result, BaseException):
                logger.warning(
                    "Hook execution failed",
                    error=str(raw_result),
                    error_type=type(raw_result).__name__,
                    hook_event=event,
                )
                continue

            result = _normalize_result(raw_result)

            if result.get("decision") == "deny":
                combined["decision"] = "deny"
            elif result.get("decision") == "ask" and combined.get("decision") != "deny":
                combined["decision"] = "ask"

            if reason := result.get("reason"):
                reasons.append(reason)

            if modified := result.get("modified_input"):
                if "modified_input" not in combined:
                    combined["modified_input"] = {}
                combined["modified_input"].update(modified)

            if "modified_output" in result:
                combined["modified_output"] = result["modified_output"]

            if ctx := result.get("additional_context"):
                contexts.append(ctx)

            if result.get("continue_") is False:
                combined["continue_"] = False

        if reasons:
            combined["reason"] = "; ".join(reasons)
        if contexts:
            combined["additional_context"] = "\n".join(contexts)

        return combined

    # -----------------------------------------------------------------------
    # Callback wrappers
    # -----------------------------------------------------------------------

    def _wrap_before_run(self) -> Callable[..., Awaitable[None]]:
        async def wrapped(ctx: RunContext[Any]) -> None:
            agent_ctx = ctx.deps
            input_data = HookInput(
                event="pre_run",
                agent_name=_get_agent_name(agent_ctx),
                session_id=_get_session_id(agent_ctx),
            )
            result = await self._combine(
                self._before_run,
                input_data,
                "before_run",
                matchers=self._matchers.get("before_run"),
                input_matchers=self._input_matchers.get("before_run"),
            )
            if result.get("decision") == "deny":
                msg = f"Run blocked: {result.get('reason', 'pre_run hook denied')}"
                raise RuntimeError(msg)

        return wrapped

    def _wrap_after_run(self) -> Callable[..., Awaitable[AgentRunResult[Any]]]:
        async def wrapped(
            ctx: RunContext[Any], *, result: AgentRunResult[Any]
        ) -> AgentRunResult[Any]:
            agent_ctx = ctx.deps
            input_data = HookInput(
                event="post_run",
                agent_name=_get_agent_name(agent_ctx),
                result=result,
                session_id=_get_session_id(agent_ctx),
            )
            await self._combine(
                self._after_run,
                input_data,
                "after_run",
                matchers=self._matchers.get("after_run"),
                input_matchers=self._input_matchers.get("after_run"),
            )
            return result

        return wrapped

    def _wrap_before_tool_execute(self) -> Callable[..., Awaitable[ValidatedToolArgs]]:
        async def wrapped(
            ctx: RunContext[Any],
            *,
            call: ToolCallPart,
            tool_def: ToolDefinition,
            args: ValidatedToolArgs,
        ) -> ValidatedToolArgs:
            agent_ctx = ctx.deps
            input_data = HookInput(
                event="pre_tool_use",
                agent_name=_get_agent_name(agent_ctx),
                tool_name=call.tool_name,
                tool_input=dict(args),
                session_id=_get_session_id(agent_ctx),
            )
            result = await self._combine(
                self._before_tool,
                input_data,
                "before_tool_execute",
                matchers=self._matchers.get("before_tool_execute"),
                input_matchers=self._input_matchers.get("before_tool_execute"),
            )
            if result.get("decision") == "deny":
                msg = f"Tool execution blocked: {result.get('reason', 'pre_tool_use hook denied')}"
                raise RuntimeError(msg)
            if modified := result.get("modified_input"):
                return {**dict(args), **modified}
            return args

        return wrapped

    def _wrap_after_tool_execute(self) -> Callable[..., Awaitable[Any]]:
        async def wrapped(
            ctx: RunContext[Any],
            *,
            call: ToolCallPart,
            tool_def: ToolDefinition,
            args: ValidatedToolArgs,
            result: Any,
        ) -> Any:
            agent_ctx = ctx.deps
            input_data = HookInput(
                event="post_tool_use",
                agent_name=_get_agent_name(agent_ctx),
                tool_name=call.tool_name,
                tool_input=dict(args),
                tool_output=result,
                duration_ms=0.0,
                session_id=_get_session_id(agent_ctx),
            )
            await self._combine(
                self._after_tool,
                input_data,
                "after_tool_execute",
                matchers=self._matchers.get("after_tool_execute"),
                input_matchers=self._input_matchers.get("after_tool_execute"),
            )
            return result

        return wrapped


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _get_agent_name(agent_ctx: Any) -> str:
    return agent_ctx.node_name if agent_ctx else ""


def _get_session_id(agent_ctx: Any) -> str | None:
    if agent_ctx and agent_ctx.run_ctx:
        session_id: str | None = agent_ctx.run_ctx.session_id
        return session_id
    return None


def _matches(
    input_data: HookInput,
    event: HookEventName,
    matcher: str | None,
    input_match: dict[str, str] | None,
) -> bool:
    if matcher and matcher != "*":
        pattern = re.compile(matcher)
        if event in ("before_tool_execute", "after_tool_execute"):
            tool_name = input_data.get("tool_name", "")
            if not pattern.search(tool_name):
                return False

    if input_match:
        tool_input = input_data.get("tool_input") or {}
        for key, pat_str in input_match.items():
            pat = re.compile(pat_str)
            value = str(tool_input.get(key, ""))
            if not pat.search(value):
                return False

    return True


async def _execute_hook(
    hook: HookCallable,
    input_data: HookInput,
    *,
    env: ExecutionEnvironment | None = None,
) -> HookResult | None:
    import inspect

    kwargs = dict(input_data)
    if env is not None:
        kwargs["env"] = env

    if inspect.iscoroutinefunction(hook):
        result = await hook(**kwargs)
    else:
        result = await asyncio.get_event_loop().run_in_executor(None, lambda: hook(**kwargs))
    return _normalize_result(result)


def _normalize_result(raw: Any) -> HookResult:
    if raw is None:
        return HookResult(decision="allow")
    if isinstance(raw, dict):
        return cast("HookResult", raw)
    if isinstance(raw, str):
        return HookResult(decision="allow", additional_context=raw)
    if isinstance(raw, bool):
        return HookResult(decision="allow" if raw else "deny")
    return HookResult(decision="allow")


def from_agent_hooks(agent_hooks: Any) -> HooksCapabilityAdapter:
    """Create a ``HooksCapabilityAdapter`` from a legacy ``AgentHooks`` instance.

    Extracts hook callables and matcher metadata from ``AgentHooks``
    (which holds ``Hook`` instances with ``fn``, ``matcher``, ``input_match``
    attributes) and constructs an adapter with equivalent behavior.
    """

    def _extract(
        hooks: Sequence[Any],
    ) -> tuple[
        list[HookCallable],
        list[str | None],
        list[dict[str, str] | None],
    ]:
        callables: list[HookCallable] = []
        matchers: list[str | None] = []
        input_matchers: list[dict[str, str] | None] = []
        for h in hooks:
            if hasattr(h, "fn"):
                callables.append(h.fn)
            elif callable(h):
                callables.append(h)
            matchers.append(getattr(h, "matcher", None))
            input_matchers.append(getattr(h, "input_match", None))
        return callables, matchers, input_matchers

    pre_run_c, pre_run_m, pre_run_im = _extract(agent_hooks.pre_run)
    post_run_c, post_run_m, post_run_im = _extract(agent_hooks.post_run)
    pre_tool_c, pre_tool_m, pre_tool_im = _extract(agent_hooks.pre_tool_use)
    post_tool_c, post_tool_m, post_tool_im = _extract(agent_hooks.post_tool_use)

    matchers: dict[HookEventName, Sequence[str | None]] = {}
    input_matchers: dict[HookEventName, Sequence[dict[str, str] | None]] = {}

    if pre_run_c:
        matchers["before_run"] = pre_run_m
        input_matchers["before_run"] = pre_run_im
    if post_run_c:
        matchers["after_run"] = post_run_m
        input_matchers["after_run"] = post_run_im
    if pre_tool_c:
        matchers["before_tool_execute"] = pre_tool_m
        input_matchers["before_tool_execute"] = pre_tool_im
    if post_tool_c:
        matchers["after_tool_execute"] = post_tool_m
        input_matchers["after_tool_execute"] = post_tool_im

    return HooksCapabilityAdapter(
        before_run=pre_run_c or None,
        after_run=post_run_c or None,
        before_tool_execute=pre_tool_c or None,
        after_tool_execute=post_tool_c or None,
        matchers=matchers or None,
        input_matchers=input_matchers or None,
    )
