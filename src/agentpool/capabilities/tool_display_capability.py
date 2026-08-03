"""ToolDisplayCapability — global decorator for tool display names and diff-rich events.

A configurable ``AbstractCapability`` that decorates the agent's fully
assembled toolset without modifying any tool or capability:

- **Rename layer** (``rename_mode``): maps selected tool names to
  display names via :class:`~pydantic_ai.toolsets.RenamedToolset`, so
  protocol clients (e.g. the OpenCode TUI) that dispatch on a whitelist
  of standard tool names render the tools properly.
- **Rich-info layer** (``emit_diff``): injects a
  :class:`~agentpool.agents.events.DiffContentItem` progress event after
  a matching tool executes, so ACP clients (e.g. Zed) render a file
  diff.

The two layers are orthogonal: an OpenCode-facing deployment uses
``rename_mode=True + emit_diff=True``; an ACP-facing deployment uses
``rename_mode=False + emit_diff=True`` (original names displayed with
diffs); a child capability that already emits its own
``DiffContentItem`` uses ``rename_mode=True + emit_diff=False`` (rename
only, no duplicate diff).

Modeled on :class:`~agentpool.agents.native_agent.tool_intercept.ToolInterceptCapability`
— a standalone ``AbstractCapability`` overriding ``get_wrapper_toolset``
and ``wrap_tool_execute`` as a global middleware over all assembled
tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset, RenamedToolset

from agentpool.agents.events import DiffContentItem


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition


def _parse_diff_fields(
    args: Mapping[str, Any], result: Any
) -> tuple[str | None, str | None, str | None]:
    """Extract (path, old_text, new_text) from tool call arguments and result.

    Recognizes common parameter shapes across file-writing tools:

    - ``path``/``file_path``/``uri`` → target path
    - ``content`` (write-style) → new text, old text ``None`` (new file)
    - ``old_string``/``new_string`` (edit-style) → old/new text pair

    ``result`` is inspected as a fallback when ``new_text`` cannot be
    derived from arguments (e.g. a tool that returns the written content
    as a string).

    Args:
        args: The validated tool call arguments.
        result: The tool execution result.

    Returns:
        A ``(path, old_text, new_text)`` tuple with ``None`` values for
        fields that could not be derived.
    """
    path = next(
        (
            str(args[k])
            for k in ("path", "file_path", "uri", "filepath")
            if isinstance(args.get(k), str) and args[k]
        ),
        None,
    )
    if path is None:
        return (None, None, None)

    old_text: str | None = None
    new_text: str | None = None
    if isinstance(args.get("old_string"), str) and isinstance(args.get("new_string"), str):
        old_text = args["old_string"]
        new_text = args["new_string"]
    elif isinstance(args.get("content"), str):
        new_text = args["content"]
    elif isinstance(result, str) and result:
        new_text = result

    return (path, old_text, new_text)


@dataclass(kw_only=True)
class ToolDisplayCapability(AbstractCapability[Any]):
    """Global tool display decorator: rename tools + inject diff events.

    Attributes:
        rename_mode: Enable tool name mapping via ``name_map``. When
            ``False``, tools keep their native names (ACP-style display).
        name_map: Mapping of native tool name to display name.
        emit_diff: Enable diff event injection after tool execution.
            When ``False``, rely on tools' own diff emission.
        emit_diff_for: Set of tool names eligible for diff event
            injection. Empty means no injection.
        id: Optional capability id (defaults to ``"tool_display"``).
    """

    id: str | None = None
    rename_mode: bool = True
    name_map: Mapping[str, str] = field(default_factory=dict)
    emit_diff: bool = True
    emit_diff_for: set[str] = field(default_factory=set)

    def get_wrapper_toolset(self, toolset: AbstractToolset[Any]) -> AbstractToolset[Any] | None:
        """Wrap the assembled toolset with ``RenamedToolset`` when enabled.

        Args:
            toolset: The agent's fully assembled toolset.

        Returns:
            A ``RenamedToolset`` applying ``name_map``, or ``None`` when
            renaming is disabled or the map is empty (toolset unchanged).
        """
        if not self.rename_mode or not self.name_map:
            return None
        return RenamedToolset(wrapped=toolset, name_map=dict(self.name_map))

    async def wrap_tool_execute(
        self,
        ctx: Any,
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> Any:
        """Execute the tool, then inject a diff progress event when enabled.

        After ``handler`` completes, derives ``(path, old_text, new_text)``
        from the call arguments and emits a
        :class:`~agentpool.agents.events.ToolCallProgressEvent` carrying a
        :class:`~agentpool.agents.events.DiffContentItem` via the run
        context's ``events`` emitter — the same channel fsspec tools use,
        which reaches ACP converters as ``FileEditToolCallContent``.

        Args:
            ctx: The pydantic-ai run context (carries ``deps`` → agentpool
                ``AgentContext`` with the ``events`` emitter).
            call: The tool call part.
            tool_def: The tool definition.
            args: The validated tool call arguments.
            handler: The wrapped tool execution callable.

        Returns:
            The tool execution result, unchanged.
        """
        result = await handler(args)

        if not self.emit_diff or not self.emit_diff_for:
            return result
        if call.tool_name not in self.emit_diff_for:
            return result

        path, old_text, new_text = _parse_diff_fields(args, result)
        if path is None or new_text is None:
            return result

        deps = getattr(ctx, "deps", None)
        events = getattr(deps, "events", None)
        if events is None:
            return result

        await events.tool_call_progress(
            title=f"Modified: {path}",
            items=[
                DiffContentItem(
                    path=path,
                    old_text=old_text,
                    new_text=new_text,
                )
            ],
        )
        return result
