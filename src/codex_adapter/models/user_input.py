from __future__ import annotations

from typing import Literal

from pydantic import Field

from codex_adapter.models.base import CodexBaseModel


class ByteRange(CodexBaseModel):
    """Byte range within a UTF-8 text buffer.

    start: Start byte offset (inclusive).
    end: End byte offset (exclusive).
    """

    start: int = Field(..., ge=0)
    end: int = Field(..., ge=0)


class TextElement(CodexBaseModel):
    """Element within text content for rich input markers.

    Used to render or persist rich input markers (e.g., image placeholders)
    across history and resume without mutating the literal text.
    """

    byte_range: ByteRange
    placeholder: str | None = None


class UserInputText(CodexBaseModel):
    """Text user input."""

    type: Literal["text"] = "text"
    text: str
    text_elements: list[TextElement] = Field(default_factory=list)


class UserInputImage(CodexBaseModel):
    """Image URL user input."""

    type: Literal["image"] = "image"
    url: str


class UserInputLocalImage(CodexBaseModel):
    """Local image file user input."""

    type: Literal["local_image"] = "local_image"
    path: str


class UserInputSkill(CodexBaseModel):
    """Skill file user input."""

    type: Literal["skill"] = "skill"
    name: str
    path: str


class UserInputMention(CodexBaseModel):
    """Mention user input."""

    type: Literal["mention"] = "mention"
    name: str
    path: str


# Discriminated union of user input types
UserInput = UserInputText | UserInputImage | UserInputLocalImage | UserInputSkill | UserInputMention
