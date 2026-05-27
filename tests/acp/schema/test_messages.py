"""Tests for ACP message schema definitions."""

from __future__ import annotations

import pytest

from acp.schema.messages import (
    AgentMethod,
    AgentNotificationMessage,
    AgentRequestMessage,
    ClientMethod,
    ClientNotificationMessage,
    ClientRequestMessage,
)


# =============================================================================
# AgentMethod literal tests
# =============================================================================


@pytest.mark.unit
def test_agent_method_accepts_standard_methods() -> None:
    """AgentMethod should accept all standard agent methods."""
    # These should all type-check and validate correctly
    methods: list[AgentMethod] = [
        "authenticate",
        "initialize",
        "providers/disable",
        "providers/list",
        "providers/set",
        "session/cancel",
        "session/close",
        "session/fork",
        "session/list",
        "session/load",
        "session/new",
        "session/prompt",
        "session/resume",
        "session/set_mode",
        "session/set_model",
    ]
    assert len(methods) == 15


@pytest.mark.unit
def test_agent_method_accepts_arbitrary_string() -> None:
    """AgentRequestMessage should accept arbitrary method strings via str fallback."""
    msg = AgentRequestMessage(method="invalid/method", params=None)
    assert msg.method == "invalid/method"


@pytest.mark.unit
def test_agent_request_message_accepts_standard_methods() -> None:
    """AgentRequestMessage should accept standard agent methods."""
    msg = AgentRequestMessage(method="session/new", params=None)
    assert msg.method == "session/new"


@pytest.mark.unit
def test_agent_notification_message_accepts_standard_methods() -> None:
    """AgentNotificationMessage should accept standard agent methods."""
    msg = AgentNotificationMessage(method="session/cancel", params=None)
    assert msg.method == "session/cancel"


# =============================================================================
# ClientMethod literal tests
# =============================================================================


@pytest.mark.unit
def test_client_method_accepts_standard_methods() -> None:
    """ClientMethod should accept all standard client methods."""
    methods: list[ClientMethod] = [
        "elicitation/create",
        "fs/read_text_file",
        "fs/write_text_file",
        "session/request_permission",
        "session/update",
        "terminal/create",
        "terminal/kill",
        "terminal/output",
        "terminal/release",
        "terminal/wait_for_exit",
    ]
    assert len(methods) == 10


@pytest.mark.unit
def test_client_method_accepts_arbitrary_string() -> None:
    """ClientRequestMessage should accept arbitrary method strings via str fallback."""
    msg = ClientRequestMessage(method="invalid/method", params=None)
    assert msg.method == "invalid/method"


@pytest.mark.unit
def test_client_request_message_accepts_standard_methods() -> None:
    """ClientRequestMessage should accept standard client methods."""
    msg = ClientRequestMessage(method="session/update", params=None)
    assert msg.method == "session/update"


@pytest.mark.unit
def test_client_notification_message_accepts_standard_methods() -> None:
    """ClientNotificationMessage should accept standard client methods."""
    msg = ClientNotificationMessage(method="session/update", params=None)
    assert msg.method == "session/update"
