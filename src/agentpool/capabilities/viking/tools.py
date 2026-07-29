"""Tool functions for the Viking capability.

Each tool is an async closure that captures the ``VikingCapability``
instance and takes a ``RunContext`` as the first parameter. All tools
wrap SDK calls in try/except and return error strings — they never raise
exceptions to the caller.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Literal
import uuid

from pydantic_ai.tools import RunContext  # noqa: TC002 - needed at runtime for get_type_hints()

from agentpool.capabilities.viking.utils import (
    add_line_numbers,
    format_ls_entries,
    format_search_results,
    truncate_text,
)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agentpool.capabilities.viking import VikingCapability


def _get_session_id(ctx: RunContext[Any]) -> str | None:
    """Extract session_id from RunContext deps if available."""
    deps = ctx.deps
    if deps is not None and hasattr(deps, "session_id"):
        return str(deps.session_id)
    return None


def build_tools(cap: VikingCapability) -> list[Callable[..., Any]]:
    """Build the list of tool functions for the Viking capability.

    The returned functions are plain async callables suitable for use
    with pydantic-ai's ``FunctionToolset``. Tools are filtered based on
    ``cap.mode``:

    - ``"retrieve"``: 7 read-only tools
    - ``"write"``: 6 write tools
    - ``"graph"``: 2 graph tools
    - ``"all"``: all 15 tools

    Args:
        cap: The ``VikingCapability`` instance that owns these tools.

    Returns:
        A list of async tool functions.
    """
    tools: list[Callable[..., Any]] = []

    # ---- Retrieve tools (7) ----

    if cap.mode in ("retrieve", "all"):

        async def viking_search(
            ctx: RunContext[Any],
            query: str,
            limit: int = 10,
            min_score: float = 0.0,
            level: str | None = None,
            target_uri: str = "",
        ) -> str:
            """Search the Viking knowledge graph semantically.

            Uses embedding-based search to find relevant content.
            Results include relevance scores and snippets.

            Args:
                query: Natural-language search query.
                limit: Maximum number of results to return.
                min_score: Minimum relevance score (0.0 to 1.0).
                level: Filter by content level (e.g. "L0", "L1", "L2").
                target_uri: Restrict search to a specific URI subtree.

            Returns:
                JSON-formatted search results.
            """
            try:
                client = await cap._ensure_client()
                sid = _get_session_id(ctx)
                sdk_filter: dict[str, str] | None = {"level": level} if level else None
                result = await client.search(
                    query,
                    target_uri=target_uri,
                    session_id=sid,
                    limit=limit,
                    score_threshold=min_score,
                    filter=sdk_filter,
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            except Exception as e:
                return f"viking_search error: {e}"

        async def viking_find(
            ctx: RunContext[Any],
            query: str,
            limit: int = 10,
            min_score: float = 0.0,
            level: str | None = None,
            target_uri: str = "",
        ) -> str:
            """Find content in Viking, deduplicating results.

            Similar to ``viking_search`` but deduplicates near-identical
            hits, returning a more diverse result set.

            Args:
                query: Natural-language search query.
                limit: Maximum number of results to return.
                min_score: Minimum relevance score (0.0 to 1.0).
                level: Filter by content level (e.g. "L0", "L1", "L2").
                target_uri: Restrict search to a specific URI subtree.

            Returns:
                JSON-formatted search results.
            """
            try:
                client = await cap._ensure_client()
                sdk_filter: dict[str, str] | None = {"level": level} if level else None
                result = await client.find(
                    query,
                    target_uri=target_uri,
                    limit=limit,
                    score_threshold=min_score,
                    filter=sdk_filter,
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            except Exception as e:
                return f"viking_find error: {e}"

        async def viking_recall(
            ctx: RunContext[Any],
            query: str,
            quotas: dict[str, int] | None = None,
            max_chars: int = 8000,
        ) -> str:
            """Recall memories from Viking across multiple context types.

            Performs multiple ``find`` calls with different context types
            and merges the results into a single formatted string.

            Valid context types are: ``memory``, ``resource``, ``skill``.
            These correspond to the three top-level namespaces in Viking:
            - ``memory``: personal memories and conversation history
            - ``resource``: ingested documents and resources
            - ``skill``: stored skill definitions

            Args:
                query: Natural-language query for memory retrieval.
                quotas: Per-context-type result limits. Valid context types
                    are ``memory``, ``resource``, ``skill``. Defaults to
                    ``{"memory": 5, "resource": 3, "skill": 2}``.
                max_chars: Maximum total characters in the output.

            Returns:
                Formatted string with recalled memories grouped by context type.
            """
            try:
                client = await cap._ensure_client()
                if quotas is None:
                    quotas = {
                        "memory": 5,
                        "resource": 3,
                        "skill": 2,
                    }
                sections: list[str] = []
                for context_type, quota in quotas.items():
                    result = await client.find(
                        query=query,
                        context_type=context_type,
                        limit=quota,
                    )
                    formatted = format_search_results(result)
                    sections.append(f"=== {context_type} ===\n{formatted}")
                merged = "\n\n".join(sections)
                return truncate_text(merged, max_chars)
            except Exception as e:
                return f"viking_recall error: {e}"

        async def viking_grep(
            ctx: RunContext[Any],
            uri: str,
            pattern: str,
            case_insensitive: bool = False,
        ) -> str:
            """Search for a regex pattern within a Viking document.

            Returns matching lines with their line numbers.

            Args:
                uri: Full viking:// URI of the document to search.
                pattern: Regular expression pattern to match.
                case_insensitive: Whether to ignore case when matching.

            Returns:
                Matching lines with line numbers, or "No matches found."
            """
            try:
                client = await cap._ensure_client()
                result = await client.grep(
                    uri,
                    pattern,
                    case_insensitive=case_insensitive,
                    node_limit=256,
                )
                if isinstance(result, dict):
                    matches = result.get("matches", result.get("results", []))
                else:
                    matches = result
                if not matches:
                    return "No matches found."
                lines: list[str] = []
                for match in matches:
                    if isinstance(match, dict):
                        line_num = match.get("line", match.get("number", "?"))
                        text = match.get("text", match.get("content", ""))
                        lines.append(f"{line_num}: {text}")
                    else:
                        lines.append(str(match))
                return "\n".join(lines)
            except Exception as e:
                return f"viking_grep error: {e}"

        async def viking_glob(
            ctx: RunContext[Any],
            pattern: str,
            uri: str = "viking://",
        ) -> str:
            """Find Viking URIs matching a glob pattern.

            Args:
                pattern: Glob pattern (e.g. ``**/*.md``).
                uri: Base URI to search from.

            Returns:
                Matching viking:// URIs, one per line.
            """
            try:
                client = await cap._ensure_client()
                result = await client.glob(
                    pattern,
                    uri=uri,
                    node_limit=256,
                )
                if isinstance(result, dict):
                    uris = result.get("uris", result.get("results", []))
                else:
                    uris = result
                if not uris:
                    return "No URIs found."
                return "\n".join(str(u) for u in uris)
            except Exception as e:
                return f"viking_glob error: {e}"

        async def viking_ls(
            ctx: RunContext[Any],
            uri: str = "",
            recursive: bool = False,
            show_abstract: bool = False,
        ) -> str:
            """List contents of a Viking directory.

            Args:
                uri: Full viking:// URI of the directory to list.
                recursive: Whether to list recursively into subdirectories.
                show_abstract: If True, fetch and display L0 abstract for each
                    directory. Costs extra API calls but helps judge directory
                    relevance.

            Returns:
                Entries with ``[dir]``/``[file]`` markers. When
                ``show_abstract=True``, directories include an L0 abstract
                after a dash separator.
            """
            try:
                client = await cap._ensure_client()
                entries = await client.ls(uri, simple=False, recursive=recursive)
                entry_list = entries if isinstance(entries, list) else []

                if show_abstract and entry_list:
                    # Fetch abstracts for directories only
                    async def _safe_abstract(entry_uri: str) -> str:
                        try:
                            return str(await client.abstract(entry_uri) or "")
                        except Exception:
                            return ""

                    abstract_uris: list[str] = []
                    abstract_tasks: list[Any] = []
                    for entry in entry_list:
                        if isinstance(entry, dict):
                            is_dir = entry.get("type") in (
                                "directory",
                                "dir",
                                "folder",
                            ) or entry.get("isDir")
                            if is_dir:
                                e_uri = str(entry.get("uri") or "")
                                if e_uri:
                                    abstract_uris.append(e_uri)
                                    abstract_tasks.append(_safe_abstract(e_uri))

                    if abstract_tasks:
                        abstracts = await asyncio.gather(*abstract_tasks)
                        abstract_map: dict[str, str] = {}
                        for e_uri, ab in zip(abstract_uris, abstracts, strict=False):
                            if isinstance(ab, str) and ab.strip():
                                abstract_map[e_uri] = ab.strip()

                        if abstract_map:
                            lines: list[str] = []
                            for entry in entry_list:
                                if isinstance(entry, dict):
                                    name = entry.get("name", entry.get("uri", "?"))
                                    entry_type = entry.get("type", "file")
                                    is_dir = entry_type in (
                                        "directory",
                                        "dir",
                                        "folder",
                                    ) or entry.get("isDir")
                                    marker = "[dir]" if is_dir else "[file]"
                                    e_uri = str(entry.get("uri") or "")
                                    ab = abstract_map.get(e_uri, "")
                                    if ab:
                                        lines.append(f"{marker} {name} — {ab}")
                                    else:
                                        lines.append(f"{marker} {name}")
                                else:
                                    lines.append(f"[file] {entry}")
                            return "\n".join(lines)

                return format_ls_entries(entry_list)
            except Exception as e:
                return f"viking_ls error: {e}"

        async def viking_read(
            ctx: RunContext[Any],
            uris: str | list[str],
            level: str = "read",
            line: int = 1,
            limit: int = -1,
        ) -> str:
            """Read content from one or more Viking URIs with tiered loading.

            Args:
                uris: A single viking:// URI or a list of URIs to read.
                level: Content depth — "abstract" (L0, ~100 tokens summary),
                    "overview" (L1, ~2k tokens structure), or "read" (L2, full
                    content). Default "read" for full content. Use "abstract"
                    for quick relevance checks or "overview" for planning without
                    loading full content.
                line: Starting line number (1-indexed, only applies when
                    level="read").
                limit: Maximum number of lines to read (-1 for all, only
                    applies when level="read").

            Returns:
                File content with line number prefixes (for level="read").
                Multiple files are separated by ``=== {uri} ===`` headers.
            """
            try:
                client = await cap._ensure_client()
                uri_list = [uris] if isinstance(uris, str) else uris
                sections: list[str] = []
                for u in uri_list:
                    if level == "abstract":
                        content = await client.abstract(u)
                    elif level == "overview":
                        content = await client.overview(u)
                    else:
                        offset = line - 1  # SDK offset is 0-indexed
                        content = await client.read(u, offset=offset, limit=limit)

                    if level == "read":
                        numbered = add_line_numbers(str(content), start_line=line)
                    else:
                        # For abstract/overview, return content without line numbers
                        numbered = str(content)

                    if len(uri_list) > 1:
                        sections.append(f"=== {u} ===\n{numbered}")
                    else:
                        sections.append(numbered)
                return "\n\n".join(sections)
            except Exception as e:
                return f"viking_read error: {e}"

        retrieve_tools: list[Callable[..., Awaitable[str]]] = [
            viking_search,
            viking_find,
            viking_grep,
            viking_glob,
            viking_ls,
            viking_read,
        ]
        if cap.enable_memory:
            retrieve_tools.append(viking_recall)
        tools.extend(retrieve_tools)

    # ---- Write tools (6) ----

    if cap.mode in ("write", "all"):

        async def viking_remember(
            ctx: RunContext[Any],
            messages: list[dict[str, str]],
        ) -> str:
            """Store a conversation experience in Viking memory.

            Creates a session, adds messages, and commits it to the
            knowledge graph for future recall.

            Args:
                messages: A list of message dicts with ``role`` and
                    ``content`` keys (e.g. ``{"role": "user", "content": "..."}``).

            Returns:
                Confirmation string with the generated session ID.
            """
            try:
                client = await cap._ensure_client()
                sid = str(uuid.uuid4())
                await client.create_session(session_id=sid)
                for msg in messages:
                    await client.add_message(sid, msg["role"], msg["content"])
                await client.commit_session(sid)
                return f"Remembered {len(messages)} messages (session: {sid})."
            except Exception as e:
                return f"viking_remember error: {e}"

        async def viking_write(
            ctx: RunContext[Any],
            uri: str,
            content: str,
            mode: Literal["create", "replace", "append"] = "create",
        ) -> str:
            """Write content to a Viking URI.

            URIs must be under ``memories/`` or ``resources/`` paths
            (e.g. ``viking://user/default/memories/notes.md`` or
            ``viking://resources/wiki/Device/SY215.md``). Other paths
            will be rejected by the backend.

            Args:
                uri: Full viking:// URI to write to. Must be under
                    ``memories/`` or ``resources/``.
                content: Content to write.
                mode: Write mode — ``"create"`` (default, fails if exists),
                    ``"replace"`` (overwrite), or ``"append"`` (add to end).

            Returns:
                Confirmation string.
            """
            try:
                client = await cap._ensure_client()
                await client.write(uri, content, mode=mode)
                return f"Wrote {len(content)} chars to {uri} (mode={mode})."
            except Exception as e:
                return f"viking_write error: {e}"

        async def viking_edit(
            ctx: RunContext[Any],
            uri: str,
            old_string: str,
            new_string: str,
            replace_all: bool = False,
        ) -> str:
            """Edit a Viking document by replacing a string.

            Uses a read-modify-write cycle: reads the current content,
            replaces ``old_string`` with ``new_string``, then writes back.
            The URI must be under ``memories/`` or ``resources/`` (same
            restriction as ``viking_write``).

            Args:
                uri: Full viking:// URI of the document to edit.
                old_string: The exact string to find and replace.
                new_string: The replacement string.
                replace_all: If ``True``, replace all occurrences. If
                    ``False``, fails if there are multiple matches.

            Returns:
                Confirmation string, or an error message if the string
                was not found or appeared multiple times.
            """
            try:
                client = await cap._ensure_client()
                current = await client.read(uri)
                count = current.count(old_string)
                if count == 0:
                    return f"viking_edit error: old_string not found in {uri}."
                if count > 1 and not replace_all:
                    return (
                        f"viking_edit error: old_string found {count} times in {uri}. "
                        "Use replace_all=True to replace all occurrences."
                    )
                if replace_all:
                    modified = current.replace(old_string, new_string)
                else:
                    modified = current.replace(old_string, new_string, 1)
                await client.write(uri, modified, mode="replace")
                return f"Replaced {count} occurrence(s) in {uri}."
            except Exception as e:
                return f"viking_edit error: {e}"

        async def viking_mkdir(
            ctx: RunContext[Any],
            uri: str,
            description: str | None = None,
        ) -> str:
            """Create a directory in the Viking knowledge graph.

            Args:
                uri: Full viking:// URI of the directory to create.
                description: Optional description for the directory.

            Returns:
                Confirmation string.
            """
            try:
                client = await cap._ensure_client()
                await client.mkdir(uri, description=description)
                return f"Created directory {uri}."
            except Exception as e:
                return f"viking_mkdir error: {e}"

        async def viking_add_resource(
            ctx: RunContext[Any],
            path: str,
            to: str | None = None,
            parent: str | None = None,
            processing_mode: str | None = None,
            watch_interval: float = 0,
        ) -> str:
            """Add an external resource to the Viking knowledge graph.

            Ingests a local file or directory into the graph, making it
            searchable and linkable. The ``to`` target must be under
            ``viking://resources/`` (e.g. ``viking://resources/wiki/``).

            Args:
                path: Local file or directory path to ingest.
                to: Target viking:// URI under ``resources/`` to store the resource.
                parent: Parent viking:// URI under ``resources/`` for nesting.
                processing_mode: Processing mode for the resource (unused by
                    current SDK — kept for API compatibility).
                watch_interval: Watch interval in seconds (0 = no watch).

            Returns:
                Confirmation string.
            """
            try:
                client = await cap._ensure_client()
                # SDK add_resource() does not accept processing_mode;
                # pass only supported kwargs.
                result = await client.add_resource(
                    path,
                    to=to,
                    parent=parent,
                    watch_interval=watch_interval,
                )
                return f"Added resource {path} to Viking. Result: {result}"
            except Exception as e:
                return f"viking_add_resource error: {e}"

        async def viking_forget(
            ctx: RunContext[Any],
            uri: str,
            recursive: bool = False,
        ) -> str:
            """Remove a document or directory from Viking.

            Args:
                uri: Full viking:// URI to remove.
                recursive: If ``True``, remove directories recursively.

            Returns:
                Confirmation string.
            """
            try:
                client = await cap._ensure_client()
                await client.rm(uri, recursive=recursive)
                return f"Removed {uri}."
            except Exception as e:
                return f"viking_forget error: {e}"

        write_tools: list[Callable[..., Awaitable[str]]] = [
            viking_write,
            viking_edit,
            viking_mkdir,
            viking_add_resource,
            viking_forget,
        ]
        if cap.enable_memory:
            write_tools.append(viking_remember)
        tools.extend(write_tools)

    # ---- Graph tools (2) ----

    if cap.mode in ("graph", "all"):

        async def viking_link(
            ctx: RunContext[Any],
            from_uri: str,
            to_uris: str | list[str],
            reason: str = "",
        ) -> str:
            """Create a link between nodes in the Viking knowledge graph.

            Both ``from_uri`` and all ``to_uris`` must point to existing
            nodes. The backend rejects links to non-existent nodes.

            Args:
                from_uri: Source viking:// URI. Must exist.
                to_uris: Target viking:// URI or list of URIs. All must exist.
                reason: Optional reason/label for the link.

            Returns:
                Confirmation string.
            """
            try:
                client = await cap._ensure_client()
                await client.link(from_uri, to_uris, reason=reason)
                targets = to_uris if isinstance(to_uris, list) else [to_uris]
                return f"Linked {from_uri} -> {', '.join(targets)} (reason: {reason!r})."
            except Exception as e:
                return f"viking_link error: {e}"

        async def viking_set_tags(
            ctx: RunContext[Any],
            uri: str,
            tags: list[str],
            recursive: bool = False,
        ) -> str:
            """Set tags on a Viking node.

            Args:
                uri: Full viking:// URI of the node to tag.
                tags: List of ``"key=value"`` tag strings.
                recursive: If ``True``, apply tags to all children recursively.

            Returns:
                Confirmation string.
            """
            try:
                client = await cap._ensure_client()
                await client.set_tags(uri, tags, mode="replace", recursive=recursive)
                return f"Set {len(tags)} tag(s) on {uri}."
            except Exception as e:
                return f"viking_set_tags error: {e}"

        graph_tools: list[Callable[..., Awaitable[str]]] = [viking_set_tags]
        if cap.enable_link:
            graph_tools.append(viking_link)
        tools.extend(graph_tools)

    return tools
