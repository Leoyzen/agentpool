"""Helper functions for formatting and validating Viking SDK results.

These utilities are used by the tool functions in ``tools.py`` to format
SDK responses into human-readable strings and to perform common text
operations like line-numbering and truncation.
"""

from __future__ import annotations

from typing import Any


def format_search_results(results: dict[str, Any] | list[Any]) -> str:
    """Format SDK search/find results as a readable string.

    Args:
        results: A dict (with ``hits``, ``results``, or Viking's grouped keys
            like ``memories``/``resources``/``skills``) or a list of hits.

    Returns:
        A formatted multi-line string. Each hit shows URI, score (if present),
        the L0 abstract (if present), and a snippet of content.
    """
    if isinstance(results, dict):
        # Viking find()/search() returns results grouped by context type
        hits: list[Any] = (
            results.get("hits")
            or results.get("results")
            or (
                results.get("memories", [])
                + results.get("resources", [])
                + results.get("skills", [])
            )
        )
    else:
        hits = results

    if not hits:
        return "No results found."

    lines: list[str] = []
    _snippet_limit = 500
    for i, hit in enumerate(hits, 1):
        if isinstance(hit, dict):
            uri = hit.get("uri", hit.get("path", "?"))
            score = hit.get("score", hit.get("similarity"))
            abstract = hit.get("abstract", "")
            content = hit.get("content", hit.get("text", hit.get("snippet", "")))
            header = f"{i}. {uri}"
            if score is not None:
                header += (
                    f" (score: {score:.4f})" if isinstance(score, float) else f" (score: {score})"
                )
            lines.append(header)
            if abstract:
                ab = str(abstract)
                if len(ab) > _snippet_limit:
                    ab = ab[:_snippet_limit] + "..."
                lines.append(f"   abstract: {ab}")
            if content:
                snippet = str(content)
                if len(snippet) > _snippet_limit:
                    snippet = snippet[:_snippet_limit] + "..."
                lines.append(f"   {snippet}")
        else:
            lines.append(f"{i}. {hit}")
    return "\n".join(lines)


def format_ls_entries(entries: list[Any]) -> str:
    """Format ls results with ``[dir]``/``[file]`` markers.

    Args:
        entries: A list of entry dicts (with ``name``, ``type``, ``uri`` keys)
            or plain strings.

    Returns:
        A formatted string with one entry per line, prefixed with
        ``[dir]`` or ``[file]``.
    """
    if not entries:
        return "(empty)"

    lines: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            name = entry.get("name", entry.get("uri", "?"))
            entry_type = entry.get("type", "file")
            marker = "[dir]" if entry_type in ("directory", "dir", "folder") else "[file]"
            lines.append(f"{marker} {name}")
        else:
            lines.append(f"[file] {entry}")
    return "\n".join(lines)


def add_line_numbers(content: str, start_line: int = 1) -> str:
    """Add line number prefixes like ``  1│ content`` to each line.

    Args:
        content: The text content to number.
        start_line: The line number for the first line (1-indexed).

    Returns:
        The content with line number prefixes.
    """
    lines = content.splitlines()
    if not lines:
        return ""
    # Calculate width for alignment based on the largest line number
    width = len(str(start_line + len(lines) - 1))
    formatted: list[str] = []
    for i, line in enumerate(lines):
        num = start_line + i
        formatted.append(f"{num:>{width}}\u2502 {line}")
    return "\n".join(formatted)


def is_viking_uri(uri: str) -> bool:
    """Check if a URI starts with ``viking://``.

    Args:
        uri: The URI string to check.

    Returns:
        ``True`` if the URI starts with ``viking://``, ``False`` otherwise.
    """
    return uri.startswith("viking://")


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate text to ``max_chars`` with an ellipsis indicator.

    If the text is shorter than ``max_chars``, it is returned unchanged.
    Otherwise, it is truncated and a ``[... truncated N chars]`` suffix
    is appended.

    Args:
        text: The text to truncate.
        max_chars: Maximum number of characters to keep.

    Returns:
        The (possibly truncated) text.
    """
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    removed = len(text) - max_chars
    return f"{truncated}\n[... truncated {removed} chars]"
