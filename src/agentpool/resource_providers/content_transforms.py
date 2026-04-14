"""Concrete ContentTransform implementations for resource content processing.

Provides ready-to-use transforms for common content processing needs:
- TruncationTransform: Smart content truncation with boundary awareness
- HeadTailTransform: Keep head and tail of content, summarize middle
- PrefixFilterTransform: Filter lines by prefix patterns
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from agentpool.resource_providers.resource_read_types import ResourceReadResult


class TruncationTransform:
    """Truncate content to a maximum character count.

    Attempts to truncate at a natural boundary (newline) near the limit
    rather than cutting mid-word or mid-line.
    """

    def __init__(
        self,
        max_chars: int,
        boundary_search: int = 200,
        suffix: str = "...",
    ) -> None:
        """Initialize TruncationTransform.

        Args:
            max_chars: Maximum characters to keep
            boundary_search: How many chars before max_chars to search for newline
            suffix: Suffix to append when truncating
        """
        self._max_chars = max_chars
        self._boundary_search = boundary_search
        self._suffix = suffix

    async def transform(self, content: str, result: ResourceReadResult) -> str:
        if len(content) <= self._max_chars:
            return content

        # Search for newline boundary near the limit
        search_start = max(0, self._max_chars - self._boundary_search)
        search_end = self._max_chars
        boundary = content.rfind("\n", search_start, search_end)

        if boundary != -1:
            return content[:boundary] + self._suffix
        return content[: self._max_chars] + self._suffix


class HeadTailTransform:
    """Keep the head and tail of content, replacing the middle with a summary.

    Useful for large files where the beginning and end contain the most
    relevant information (e.g., config files, logs).
    """

    def __init__(
        self,
        head_chars: int = 5000,
        tail_chars: int = 5000,
        separator: str = "\n\n... [middle section omitted] ...\n\n",
    ) -> None:
        self._head_chars = head_chars
        self._tail_chars = tail_chars
        self._separator = separator

    async def transform(self, content: str, result: ResourceReadResult) -> str:
        threshold = self._head_chars + self._tail_chars + len(self._separator)
        if len(content) <= threshold:
            return content

        return content[: self._head_chars] + self._separator + content[-self._tail_chars :]


class PrefixFilterTransform:
    """Filter content lines by prefix patterns.

    Keeps only lines that DO NOT start with any of the excluded prefixes.
    Useful for filtering out comments, metadata, or noise from content.
    """

    def __init__(self, exclude_prefixes: tuple[str, ...] = ("#", "//", "/*")) -> None:
        self._exclude_prefixes = exclude_prefixes

    async def transform(self, content: str, result: ResourceReadResult) -> str:
        lines = content.split("\n")
        filtered = [
            line
            for line in lines
            if not any(line.lstrip().startswith(prefix) for prefix in self._exclude_prefixes)
        ]
        return "\n".join(filtered)
