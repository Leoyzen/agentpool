"""Tool functions for the AgentDB capability.

Phase 1: Schema-agnostic read tools (search/read/ls/grep) that proxy
to the composed VikingCapability with URI prefix visibility enforcement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import RunContext  # noqa: TC002 - needed for get_type_hints()

from agentpool.capabilities.agent_db.visibility import URIPrefixFilter


if TYPE_CHECKING:
    from collections.abc import Callable

    from agentpool.capabilities.agent_db import AgentDBCapability


def _get_session_id(ctx: RunContext[Any]) -> str | None:
    """Extract session_id from RunContext deps if available."""
    deps = ctx.deps
    if deps is not None and hasattr(deps, "session_id"):
        return str(deps.session_id)
    return None


def build_tools(cap: AgentDBCapability) -> list[Callable[..., Any]]:
    """Build the list of tool functions for the AgentDB capability.

    Phase 1: Returns schema-agnostic read tools (search/read/ls/grep).

    Args:
        cap: The AgentDBCapability instance that owns these tools.

    Returns:
        A list of async tool functions.
    """
    tools: list[Callable[..., Any]] = []
    uri_filter = URIPrefixFilter(allowed_prefixes=cap.allowed_prefixes)

    if cap.mode in ("read", "write", "all"):

        async def agentdb_search(
            ctx: RunContext[Any],
            query: str,
            uri: str = "",
            limit: int = 10,
            min_score: float = 0.35,
        ) -> ToolReturn:
            """Search the knowledge base semantically.

            Uses embedding-based search to find relevant content within
            the allowed URI namespaces. Results include relevance scores
            and snippets.

            Args:
                query: Natural-language search query.
                uri: Restrict search to a URI subtree (e.g. viking://wiki/).
                    Empty string searches all allowed namespaces.
                limit: Maximum number of results to return.
                min_score: Minimum relevance score (0.0 to 1.0).

            Returns:
                Formatted search results.
            """
            search_uri = uri if uri else cap.allowed_prefixes[0]
            if not uri_filter.is_allowed(search_uri):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{search_uri}' is not in the allowed "
                        f"namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                sid = _get_session_id(ctx)
                result = await client.search(
                    query,
                    target_uri=search_uri,
                    session_id=sid,
                    limit=limit,
                    score_threshold=min_score,
                )
                results = result.get("results", []) if isinstance(result, dict) else []
                if not results:
                    return ToolReturn(return_value="No results found.")
                lines: list[str] = []
                for i, r in enumerate(results, 1):
                    uri_str = r.get("uri", "?")
                    score = r.get("score", 0.0)
                    abstract = r.get("abstract", "")[:200]
                    lines.append(f"{i}. [{score:.2f}] {uri_str}\n   {abstract}")
                return ToolReturn(return_value="\n".join(lines))
            except Exception as e:
                return ToolReturn(return_value=f"Search error: {e}")

        tools.append(agentdb_search)

        async def agentdb_read(
            ctx: RunContext[Any],
            uri: str,
            level: int = 2,
        ) -> ToolReturn:
            """Read content from the knowledge base at a specified detail level.

            Supports L0 (abstract/metadata), L1 (overview/summary), and
            L2 (full content) loading.

            Args:
                uri: Viking URI to read (e.g. viking://wiki/fault/test.md).
                level: Loading level — 0=metadata, 1=summary, 2=full content.

            Returns:
                The content at the requested level.
            """
            if not uri_filter.is_allowed(uri):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{uri}' is not in the allowed "
                        f"namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                if level == 0:
                    content = await client.abstract(uri)
                elif level == 1:
                    content = await client.overview(uri)
                else:
                    content = await client.read(uri)
                return ToolReturn(return_value=content if content else "File empty or not found.")
            except Exception as e:
                return ToolReturn(return_value=f"Read error: {e}")

        tools.append(agentdb_read)

        async def agentdb_ls(
            ctx: RunContext[Any],
            uri: str,
        ) -> ToolReturn:
            """List files and subdirectories under a URI.

            Args:
                uri: Directory URI to list (e.g. viking://wiki/fault/).

            Returns:
                Formatted list of entries.
            """
            if not uri_filter.is_allowed(uri):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{uri}' is not in the allowed "
                        f"namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                entries = await client.ls(uri)
                if not entries:
                    return ToolReturn(return_value="Directory empty or not found.")
                lines: list[str] = []
                for entry in entries:
                    if isinstance(entry, str):
                        lines.append(entry)
                    elif isinstance(entry, dict):
                        name = entry.get("name", "?")
                        is_dir = entry.get("is_dir", False)
                        prefix = "\U0001f4c1 " if is_dir else "\U0001f4c4 "
                        lines.append(f"{prefix}{name}")
                    else:
                        lines.append(str(entry))
                return ToolReturn(return_value="\n".join(lines))
            except Exception as e:
                return ToolReturn(return_value=f"ls error: {e}")

        tools.append(agentdb_ls)

        async def agentdb_grep(
            ctx: RunContext[Any],
            pattern: str,
            uri: str = "",
            case_insensitive: bool = False,
        ) -> ToolReturn:
            """Search file contents with a regex pattern.

            Args:
                pattern: Regular expression pattern to search for.
                uri: URI subtree to search (e.g. viking://wiki/).
                    Empty string searches all allowed namespaces.
                case_insensitive: Whether to ignore case.

            Returns:
                Formatted grep results with matching lines and locations.
            """
            search_uri = uri if uri else cap.allowed_prefixes[0]
            if not uri_filter.is_allowed(search_uri):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{search_uri}' is not in the allowed "
                        f"namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                result = await client.grep(
                    pattern,
                    uri=search_uri,
                    case_insensitive=case_insensitive,
                )
                matches = result.get("matches", []) if isinstance(result, dict) else []
                if not matches:
                    return ToolReturn(return_value="No matches found.")
                lines: list[str] = []
                for m in matches:
                    file_uri = m.get("uri", "?")
                    line_text = m.get("line", "")
                    lineno = m.get("lineno", 0)
                    lines.append(f"{file_uri}:{lineno}: {line_text}")
                return ToolReturn(return_value="\n".join(lines))
            except Exception as e:
                return ToolReturn(return_value=f"grep error: {e}")

        tools.append(agentdb_grep)

    # Phase 2: Schema-aware read tools
    from agentpool.capabilities.agent_db.schema_read import build_schema_read_tools

    tools.extend(build_schema_read_tools(cap))

    # Phase 5: Advanced read tools
    from agentpool.capabilities.agent_db.schema_advanced import build_advanced_read_tools

    tools.extend(build_advanced_read_tools(cap))

    # Phase 4: Schema-aware write tools (gated by mode)
    from agentpool.capabilities.agent_db.schema_write import build_schema_write_tools

    tools.extend(build_schema_write_tools(cap))

    # Phase 5: Feedback tools (available in ALL modes)
    from agentpool.capabilities.agent_db.schema_feedback import build_feedback_tools

    tools.extend(build_feedback_tools(cap))

    return tools
