"""Pydantic models for Codex JSON-RPC API requests and responses."""

from __future__ import annotations

from typing import Literal

from codex_adapter.models.base import CodexBaseModel


class CommandActionRead(CodexBaseModel):
    """Read command action."""

    type: Literal["read"] = "read"
    command: str
    name: str
    path: str


class CommandActionListFiles(CodexBaseModel):
    """List files command action."""

    type: Literal["listFiles"] = "listFiles"
    command: str
    path: str | None = None


class CommandActionSearch(CodexBaseModel):
    """Search command action."""

    type: Literal["search"] = "search"
    command: str
    query: str | None = None
    path: str | None = None


class CommandActionUnknown(CodexBaseModel):
    """Unknown command action."""

    type: Literal["unknown"] = "unknown"
    command: str


# Discriminated union of command actions
CommandAction = (
    CommandActionRead | CommandActionListFiles | CommandActionSearch | CommandActionUnknown
)
