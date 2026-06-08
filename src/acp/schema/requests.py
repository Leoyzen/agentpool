"""Shared request schema definitions."""

from __future__ import annotations

from typing import Literal

from acp.schema.base import AnnotatedObject


class PromptDelegation(AnnotatedObject):
    """Delegation configuration for a prompt request.

    Controls whether and how the agent may delegate this prompt to a subagent.
    """

    policy: Literal["auto", "disable", "prefer", "require"]
    """Delegation policy.

    - ``auto``: Agent decides whether to delegate (default behavior).
    - ``disable``: Do not delegate; process locally.
    - ``prefer``: Prefer delegation if a suitable subagent is available.
    - ``require``: Must delegate to a subagent.
    """

    subagent_id: str | None = None
    """Optional specific subagent to delegate to.

    When ``policy`` is ``prefer`` or ``require``, this may be set to target
    a specific subagent. If ``None``, the agent selects an appropriate subagent.
    """

    run_mode: Literal["foreground", "background"] | None = None
    """Optional execution mode for the delegated subagent.

    - ``foreground``: Synchronous execution; parent waits for completion.
    - ``background``: Asynchronous execution; parent receives a handle.
    """
