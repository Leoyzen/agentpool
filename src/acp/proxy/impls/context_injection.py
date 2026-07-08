"""ContextInjectionProxy — intercepts session/prompt to prepend context.

Injects AGENTS.md content and skill instructions before the agent's prompt.
Must NOT conflate with HookProxy's additional_context.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


class ContextInjectionProxy:
    """Proxy that injects context (AGENTS.md, skills) into session/prompt.

    Implements the Proxy protocol defined in acp.proxy.protocol.
    """

    def __init__(
        self,
        agents_md_path: str | None = None,
        skill_instructions: list[str] | None = None,
    ) -> None:
        """Initialize the ContextInjectionProxy.

        Args:
            agents_md_path: Path to AGENTS.md file. If None, looks in cwd.
            skill_instructions: List of skill instruction strings to inject.
        """
        self._agents_md_path = agents_md_path
        self._skill_instructions = skill_instructions or []

    def proxy_initialize(self) -> list[str]:
        """Return the list of ACP methods this proxy intercepts.

        Returns:
            List of intercepted method names.
        """
        return ["session/prompt"]

    async def proxy_successor(
        self,
        method: str,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Inject context into session/prompt requests.

        For session/prompt: prepends AGENTS.md and skill instructions.
        For all other methods: passes through unchanged.

        Args:
            method: The ACP method name.
            params: The method parameters.
            meta: Message metadata.

        Returns:
            Modified params with injected context.
        """
        if method != "session/prompt":
            return params

        # Skip injection for responses
        if meta.get("response", False):
            return params

        context_parts: list[str] = []

        # Inject AGENTS.md content
        agents_content = self._read_agents_md()
        if agents_content:
            context_parts.append(agents_content)

        # Inject skill instructions
        context_parts.extend(self._skill_instructions)

        if not context_parts:
            return params

        # Prepend context to prompt content
        context_text = "\n\n".join(context_parts)
        content_list: Any = params.get("content", [])
        if isinstance(content_list, list):
            content_list.insert(0, {"type": "text", "text": context_text})
            params["content"] = content_list
        elif isinstance(content_list, str):
            params["content"] = context_text + "\n\n" + content_list

        return params

    def _read_agents_md(self) -> str | None:
        """Read AGENTS.md content from the configured or default path.

        Returns:
            File content as string, or None if file not found.
        """
        path_str = self._agents_md_path
        if path_str is None:
            # Default: look in current directory
            path_str = "AGENTS.md"

        path = Path(path_str)
        if not path.exists():
            logger.debug("AGENTS.md not found at %s, skipping injection", path)
            return None

        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read AGENTS.md at %s: %s", path, exc)
            return None
