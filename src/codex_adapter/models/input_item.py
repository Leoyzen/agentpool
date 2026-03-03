from __future__ import annotations

from typing import Literal, Self

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

    @classmethod
    def from_bytes(cls, data: bytes, media_type: str) -> Self:
        import base64

        b64 = base64.b64encode(data).decode()
        data_uri = f"data:{media_type};base64,{b64}"
        return cls(url=data_uri)


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
