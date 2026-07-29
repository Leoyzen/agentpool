"""Shared resource resolution utility — single entry point for reading resource content by URI.

Both the OpenCode converter (``_resolve_resource()``) and ``ResourceCapability.read_resource()``
tool delegate to ``resolve_resource_content()`` to avoid logic duplication.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

import logfire
from pydantic_ai import BinaryContent

from agentpool.capabilities.resource_protocols import (
    BlobResourceContent,
    TextResourceContent,
)


if TYPE_CHECKING:
    from pydantic_ai.messages import UserContent

    from agentpool.capabilities.resource_protocols import ResourceAccess, SkillResource


def _truncate_text(text: str, max_chars: int) -> str:
    """Truncate text to ``max_chars`` if needed, appending a truncation suffix.

    Args:
        text: The text to potentially truncate.
        max_chars: Maximum number of characters to keep.

    Returns:
        The original text if within limit, or a truncated version with suffix.
    """
    if len(text) <= max_chars:
        return text
    suffix = f"\n\n... [truncated: {len(text)} chars total, showing first {max_chars}]"
    return text[:max_chars] + suffix


def _extract_skill_name(uri: str) -> str:
    """Extract the skill name from a ``skill://`` URI.

    Takes the first path segment after ``skill://``.

    Args:
        uri: A ``skill://`` URI.

    Returns:
        The skill name (first path segment).
    """
    path = uri[len("skill://") :]
    return path.split("/")[0] if path else ""


@logfire.instrument("capability.resource_resolver.resolve")
async def resolve_resource_content(
    uri: str,
    resource_caps: list[ResourceAccess],
    skill_caps: list[SkillResource],
    *,
    max_text_chars: int = 10_000,
) -> list[UserContent] | None:
    """Resolve a resource URI and return its content as ``UserContent`` items.

    Routes by URI scheme:
        - ``skill://`` → ``SkillResource`` providers (``read_skill()``)
        - Other URIs → ``ResourceAccess`` providers (``read_resource()``)

    Args:
        uri: The resource URI to resolve.
        resource_caps: List of ``ResourceAccess`` providers to query for non-skill URIs.
        skill_caps: List of ``SkillResource`` providers to query for ``skill://`` URIs.
        max_text_chars: Maximum text characters before truncation.

    Returns:
        A list of ``UserContent`` items (strings and/or ``BinaryContent``) if the
        resource was found, or ``None`` if no provider could resolve the URI.
    """
    # ---- skill:// routing ----
    if uri.startswith("skill://"):
        skill_name = _extract_skill_name(uri)
        for skill_cap in skill_caps:
            try:
                content = await skill_cap.read_skill(skill_name)
            except Exception:  # noqa: BLE001
                logfire.exception(
                    "Failed to read skill '{skill_name}' from {cap}",
                    skill_name=skill_name,
                    cap=type(skill_cap).__name__,
                )
                continue
            if content is None:
                continue
            truncated = _truncate_text(content, max_text_chars)
            return [f'<resource uri="{uri}">\n{truncated}\n</resource>']
        return None

    # ---- Other URI schemes → ResourceAccess providers ----
    for resource_cap in resource_caps:
        try:
            contents = await resource_cap.read_resource(uri)
        except Exception:  # noqa: BLE001
            logfire.exception(
                "Failed to read resource '{uri}' from {cap}",
                uri=uri,
                cap=type(resource_cap).__name__,
            )
            continue
        if contents is None:
            continue
        if not contents:
            continue

        parts: list[UserContent] = []
        for c in contents:
            if isinstance(c, TextResourceContent):
                truncated = _truncate_text(c.text, max_text_chars)
                parts.append(f'<resource uri="{uri}">\n{truncated}\n</resource>')
            elif isinstance(c, BlobResourceContent):
                decoded = base64.b64decode(c.blob)
                media_type = c.mime_type or "application/octet-stream"
                parts.append(f'<resource uri="{uri}">\n')
                parts.append(BinaryContent(data=decoded, media_type=media_type))
                parts.append("\n</resource>")
        if parts:
            return parts

    return None
