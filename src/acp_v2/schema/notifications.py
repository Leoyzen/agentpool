"""ACP v2 notification schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from acp.schema.base import AnnotatedObject
from acp_v2.schema.session_updates import SessionUpdate


class SessionNotification(AnnotatedObject):
    """Wrapper for a v2 session/update notification."""

    session_id: str = Field(alias="sessionId")
    update: SessionUpdate


class CancelNotification(AnnotatedObject):
    """Cancel ongoing operations in a session."""

    session_id: str = Field(alias="sessionId")


class ExtNotification(AnnotatedObject):
    """Extension notification for custom methods."""

    method: str
    params: dict[str, Any] | None = None


AgentNotification = SessionNotification | ExtNotification
ClientNotification = CancelNotification | ExtNotification
