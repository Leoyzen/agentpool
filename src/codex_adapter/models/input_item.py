from __future__ import annotations

from typing import Literal

from codex_adapter.models.base import CodexBaseModel


class TextInputItem(CodexBaseModel):
    """Text input for a turn."""

    type: Literal["text"] = "text"
    text: str


class LocalImageInputItem(CodexBaseModel):
    """Local image file input for a turn."""

    type: Literal["localImage"] = "localImage"
    path: str


class ImageInputItem(CodexBaseModel):
    """Image URL input for a turn."""

    type: Literal["image"] = "image"
    url: str


class SkillInputItem(CodexBaseModel):
    """Skill input for a turn."""

    type: Literal["skill"] = "skill"
    name: str
    path: str


class MentionInputItem(CodexBaseModel):
    """Mention input for a turn."""

    type: Literal["mention"] = "mention"
    name: str
    path: str


# Discriminated union of input types
TurnInputItem = (
    TextInputItem | LocalImageInputItem | ImageInputItem | SkillInputItem | MentionInputItem
)
