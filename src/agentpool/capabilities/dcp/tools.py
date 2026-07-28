"""Context pruning tool functions for model-invoked pruning actions.

Provides ``prune_tool``, ``distill_tool``, and ``decompress_tool`` that
the model calls to record pruning decisions.  Actions are deferred —
they are appended to ``DCPState.pending_actions`` and applied later by
the capability's Phase 3 pipeline.

``prune_tool`` and ``distill_tool`` use numeric string IDs that map to
``tool_call_id`` values via ``state.tool_id_list``.  The model sees a
``<prunable-tools>`` numbered list and passes IDs like ``["0", "2"]``.

``decompress_tool`` restores original content from pruned
``ToolReturnPart`` metadata without modifying message history.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Annotated, TypedDict
from uuid import uuid4

from pydantic_ai import ModelRetry
from pydantic_ai.messages import ToolCallPart, ToolReturnPart

from agentpool.capabilities.dcp.state import (
    DCPState,
    DistillTarget,
    PruneAction,
)
from agentpool.capabilities.dcp.strategies import _is_pruned


if TYPE_CHECKING:
    from pydantic_ai import RunContext

    from agentpool.agents.context import AgentContext


class DistillTargetInput(TypedDict):
    """Input schema for a single distill target.

    Provides pydantic-ai with a proper JSON schema so the model can
    discover the correct field names without guessing.

    Attributes:
        id: Numeric string ID from the ``<prunable-tools>`` list.
        distillation: Complete technical substitute for the tool output.
    """

    id: Annotated[str, "Numeric ID (as string) from the <prunable-tools> list"]
    distillation: Annotated[str, "Complete technical distillation for this tool output"]


logger = logging.getLogger(__name__)

_DCP_METADATA_KEY = "dcp"

_RETRY_LIST_SUMMARY_MAX_LEN = 60


def _build_valid_id_list(state: DCPState) -> str:
    """Build a quick summary of valid tool IDs for ``ModelRetry`` messages.

    Iterates ``state.current_messages`` and matches ``ToolReturnPart``
    instances against ``state.tool_id_list`` (which already excludes
    protected tools).  Produces a formatted list the model can use to
    retry with correct IDs.

    Args:
        state: The DCP state with ``tool_id_list`` and
            ``current_messages``.

    Returns:
        Formatted string listing valid IDs, or a message indicating
        no prunable tools are available.
    """
    if not state.tool_id_list or state.current_messages is None:
        return "No prunable tool outputs available."

    id_map: dict[str, int] = {tcid: idx for idx, tcid in enumerate(state.tool_id_list)}

    # Build a map of tool_call_id -> call args for identification.
    call_args_map: dict[str, str] = {}
    for msg in state.current_messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, ToolCallPart) and part.tool_call_id:
                call_args_map[part.tool_call_id] = str(part.args)

    entries: list[str] = []

    for msg in state.current_messages:
        for part in getattr(msg, "parts", []):
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_call_id not in id_map:
                continue
            numeric_id = id_map[part.tool_call_id]
            if _is_pruned(part):
                entries.append(f"  {numeric_id}: {part.tool_name} [pruned]")
            else:
                summary = call_args_map.get(part.tool_call_id, "")
                if not summary:
                    summary = str(part.content)
                summary = summary[:_RETRY_LIST_SUMMARY_MAX_LEN]
                entries.append(f"  {numeric_id}: {part.tool_name}, {summary}")

    if not entries:
        return "No prunable tool outputs available."
    return "Valid prunable-tool IDs:\n" + "\n".join(entries)


def _normalize_content(content: object) -> str:
    """Normalize ``ToolReturnPart`` content to a string for substring search.

    For ``str`` content: returned as-is.
    For non-str content: serialized via ``json.dumps`` with
    ``ensure_ascii=False`` and ``default=str``.

    Args:
        content: The raw content from a ``ToolReturnPart``.

    Returns:
        String representation of the content.
    """
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _get_dcp_state(ctx: RunContext[AgentContext]) -> DCPState:
    """Get or create ``DCPState`` from the run context.

    Uses the session-metadata pattern: state is stored in
    ``SessionData.metadata['dcp']``.

    Args:
        ctx: The run context with agent dependencies.

    Returns:
        The mutable ``DCPState`` for this session.

    Raises:
        RuntimeError: If session state is unavailable.
    """
    session_data = ctx.deps.get_session_state()
    if session_data is None:
        msg = "Session state not available for context pruning"
        raise RuntimeError(msg)
    state = session_data.metadata.get(_DCP_METADATA_KEY)
    if isinstance(state, DCPState):
        return state
    new_state = DCPState.from_dict(state) if isinstance(state, dict) else DCPState()
    session_data.metadata[_DCP_METADATA_KEY] = new_state
    return new_state


def prune_tool(
    ctx: RunContext[AgentContext],
    ids: list[str] | None = None,
    reason: str | None = None,
    clear_thinking: bool | None = None,
) -> dict[str, object]:
    """Record a prune action to remove tool outputs and/or toggle thinking clearing.

    Each ID is a numeric string (e.g. ``"0"``, ``"2"``) that maps to a
    ``tool_call_id`` via ``state.tool_id_list``.  The action is deferred
    — it is appended to ``pending_actions`` and applied later by the
    capability's Phase 3 pipeline.  Tools do NOT modify messages directly.

    When ``clear_thinking`` is not ``None``, it toggles the persistent
    ``clear_thinking_active`` flag in ``DCPState``.  When ``True``,
    ``ThinkingPart`` content is stripped from assistant messages before
    the last user message on every subsequent pipeline run.  When
    ``False``, the stripping is disabled.

    Both ``ids`` and ``clear_thinking`` can be provided in the same call
    to prune tool outputs and toggle thinking clearing simultaneously.

    Args:
        ctx: The run context with agent dependencies.
        ids: Numeric string IDs from the ``<prunable-tools>`` list.
            If ``None``, no tool-output pruning is performed.
        reason: Optional reason for pruning (used for logging only).
        clear_thinking: When ``True``, enable persistent thinking-content
            stripping.  When ``False``, disable it.  When ``None`` (default),
            no change to the thinking-clearing flag.

    Returns:
        Dict with ``status``, ``action``, and details about what was
        applied.

    Raises:
        ModelRetry: If both ``ids`` and ``clear_thinking`` are ``None``
            (nothing to do), or if ``ids`` is empty, or if any ID is out
            of range or non-numeric.
    """
    if ids is None and clear_thinking is None:
        raise ModelRetry(
            "Provide either `ids` to prune tool outputs or `clear_thinking` "
            "(true/false) to toggle thinking-content stripping.",
        )

    state = _get_dcp_state(ctx)
    result: dict[str, object] = {"status": "applied"}
    actions_taken: list[str] = []

    # --- Handle clear_thinking toggle ---
    if clear_thinking is not None:
        prev = state.clear_thinking_active
        state.clear_thinking_active = clear_thinking
        actions_taken.append(
            f"clear_thinking {'enabled' if clear_thinking else 'disabled'} "
            f"(was {'enabled' if prev else 'disabled'})",
        )
        logger.debug(
            "prune_tool: clear_thinking toggled: %s -> %s (turn=%d)",
            prev,
            clear_thinking,
            state.current_turn,
        )

    # --- Handle ids-based pruning ---
    if ids is not None:
        if not ids:
            raise ModelRetry("ids cannot be empty")

        tool_id_list = state.tool_id_list

        if not tool_id_list:
            raise ModelRetry(
                "No prunable tool outputs found in context. "
                "There are no tool results available to prune.",
            )

        mapped_ids: list[str] = []
        for id_str in ids:
            try:
                idx = int(id_str)
            except ValueError:
                upper = len(tool_id_list) - 1
                raise ModelRetry(
                    f"id {id_str!r} is not a valid number. Valid range: 0-{upper}",
                ) from None

            if idx < 0 or idx >= len(tool_id_list):
                upper = len(tool_id_list) - 1
                raise ModelRetry(
                    f"id {id_str} is out of range. Valid range: 0-{upper}\n"
                    f"{_build_valid_id_list(state)}",
                ) from None

            mapped_ids.append(tool_id_list[idx])

        action_id = str(uuid4())
        action = PruneAction(
            kind="prune",
            ids=tuple(mapped_ids),
            source_tool_call_id=action_id,
        )
        state.pending_actions.append(action)
        actions_taken.append(f"pruned {len(mapped_ids)} tool output(s): ID(s) {', '.join(ids)}")
        logger.debug(
            "prune_tool: turn=%d ids=%s action_id=%s reason=%s pending=%d",
            state.current_turn,
            ids,
            action_id,
            reason,
            len(state.pending_actions),
        )
        result["count"] = len(mapped_ids)
        result["pruned_ids"] = ids
        result["total_prunable"] = len(tool_id_list)

    result["action"] = ", ".join(actions_taken)
    result["message"] = "; ".join(actions_taken) + "."
    return result


def distill_tool(
    ctx: RunContext[AgentContext],
    targets: list[DistillTargetInput],
) -> dict[str, object]:
    """Record a distill action to replace tool outputs with summaries.

    Each target is a ``DistillTargetInput`` with ``id`` (numeric string)
    and ``distillation`` (summary text).  IDs map to ``tool_call_id``
    values via ``state.tool_id_list``.

    The action is deferred — it is appended to ``pending_actions`` and
    applied later by the capability's Phase 3 pipeline.  Tools do NOT
    modify messages directly.

    Args:
        ctx: The run context with agent dependencies.
        targets: List of ``DistillTargetInput`` dicts.

    Returns:
        Dict with ``queued`` (number of targets queued) and
        ``invalid_ids`` (always empty — invalid IDs raise
        ``ModelRetry``).

    Raises:
        ModelRetry: If ``targets`` is empty, if any target is missing
            ``distillation`` or has an empty value, or if any ID is
            out of range or non-numeric.
    """
    if not targets:
        raise ModelRetry("targets cannot be empty")

    state = _get_dcp_state(ctx)
    tool_id_list = state.tool_id_list

    if not tool_id_list:
        raise ModelRetry(
            "No prunable tool outputs found in context. "
            "There are no tool results available to distill.",
        )

    distill_targets: list[DistillTarget] = []
    for target in targets:
        try:
            id_val = target["id"]
            distillation = target["distillation"]
        except KeyError as e:
            raise ModelRetry(f"target missing required field {e}") from None

        if not distillation:
            raise ModelRetry("distillation cannot be empty")

        if not isinstance(id_val, str):
            raise ModelRetry(
                f"target id must be a string, got {type(id_val).__name__}",
            )

        try:
            idx = int(id_val)
        except ValueError:
            upper = len(tool_id_list) - 1
            raise ModelRetry(
                f"target id {id_val!r} is not a valid number. Valid range: 0-{upper}",
            ) from None

        if idx < 0 or idx >= len(tool_id_list):
            upper = len(tool_id_list) - 1
            raise ModelRetry(
                f"target id {id_val} is out of range. Valid range: 0-{upper}\n"
                f"{_build_valid_id_list(state)}",
            )

        distill_targets.append(
            DistillTarget(
                tool_call_id=tool_id_list[idx],
                distillation=str(distillation),
            ),
        )

    action_id = str(uuid4())
    action = PruneAction(
        kind="distill",
        targets=tuple(distill_targets),
        source_tool_call_id=action_id,
    )
    state.pending_actions.append(action)
    logger.debug(
        "distill_tool: turn=%d targets=%d action_id=%s pending=%d",
        state.current_turn,
        len(distill_targets),
        action_id,
        len(state.pending_actions),
    )
    distilled_ids = [t["id"] for t in targets]
    id_str = ", ".join(distilled_ids)
    return {
        "status": "applied",
        "action": "distill",
        "count": len(distill_targets),
        "distilled_ids": distilled_ids,
        "message": (
            f"Distilled {len(distill_targets)} tool output(s): ID(s) {id_str}. "
            "Original content replaced with distillation. Use decompress to restore if needed."
        ),
    }


def decompress_tool(
    ctx: RunContext[AgentContext],
    tool_id: str,
) -> dict[str, object]:
    """Restore original content from a pruned tool output by numeric ID.

    Maps the numeric ``id`` to a ``tool_call_id`` via
    ``state.tool_id_list``, then searches ``state.current_messages``
    for the corresponding ``ToolReturnPart``.  If found and already
    pruned (via ``_is_pruned``), returns the original content stored
    in metadata.

    Does NOT modify message history — the model receives the original
    content as a tool return and can decide how to use it.

    Args:
        ctx: The run context with agent dependencies.
        tool_id: Numeric ID (as string) from the ``<prunable-tools>`` list.
            The ID must correspond to a ``[pruned]`` entry.

    Returns:
        If pruned: ``{"id": ..., "original_content": ...,
        "was_pruned_as": ..., "restored": True}``.
        If not found or not pruned: ``{"id": ...,
        "restored": False, "reason": "..."}``.
        If messages unavailable: ``{"id": ...,
        "restored": False, "reason": "no messages available"}``.
    """
    state = _get_dcp_state(ctx)
    tool_id_list = state.tool_id_list

    if not tool_id_list:
        raise ModelRetry(
            "No tool outputs found in context to decompress.",
        )

    try:
        idx = int(tool_id)
    except ValueError:
        upper = len(tool_id_list) - 1
        raise ModelRetry(
            f"id {tool_id!r} is not a valid number. Valid range: 0-{upper}",
        ) from None

    if idx < 0 or idx >= len(tool_id_list):
        upper = len(tool_id_list) - 1
        raise ModelRetry(
            f"id {tool_id} is out of range. Valid range: 0-{upper}\n{_build_valid_id_list(state)}",
        )

    tool_call_id = tool_id_list[idx]

    if state.current_messages is None:
        return {
            "id": tool_id,
            "restored": False,
            "reason": "no messages available",
        }

    for msg in state.current_messages:
        for part in getattr(msg, "parts", []):
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_call_id != tool_call_id:
                continue
            if not _is_pruned(part):
                return {
                    "id": tool_id,
                    "restored": False,
                    "reason": "tool is not pruned — nothing to decompress",
                }
            # Found and pruned — extract original content from metadata.
            md = part.metadata
            if isinstance(md, dict):
                return {
                    "id": tool_id,
                    "original_content": md.get("_prune_original", ""),
                    "was_pruned_as": md.get("_prune_kind", ""),
                    "restored": True,
                }
            return {
                "id": tool_id,
                "restored": False,
                "reason": "metadata not found",
            }

    # Not found in any message.
    return {
        "id": tool_id,
        "restored": False,
        "reason": "not found in message history",
    }
