"""Tests for v2 capabilities and initialize schemas."""

from __future__ import annotations

import pytest
from acp_v2.schema import (
    Capabilities,
    ClientMethod,
    InitializeRequest,
    InitializeResponse,
    LoginAuthRequest,
    LogoutAuthRequest,
    PromptResponse,
)


class TestCapabilities:
    """Verify v2 unified capabilities object markers."""

    @pytest.mark.unit
    def test_empty_capabilities(self) -> None:
        caps = Capabilities.empty()
        dumped = caps.model_dump(by_alias=True, exclude_none=True)
        assert dumped == {}

    @pytest.mark.unit
    def test_session_prompt_supported(self) -> None:
        from acp_v2.schema.capabilities import PromptCapabilities, SessionCapabilities
        caps = Capabilities(
            session=SessionCapabilities(prompt=PromptCapabilities()),
        )
        dumped = caps.model_dump(by_alias=True, exclude_none=True)
        assert "session" in dumped
        assert "prompt" in dumped["session"]


class TestInitializeRequest:
    """Verify v2 initialize uses unified capabilities + info."""

    @pytest.mark.unit
    def test_initialize_with_protocol_version_2(self) -> None:
        req = InitializeRequest(protocol_version=2)
        dumped = req.model_dump(by_alias=True, exclude_none=True)
        assert dumped["protocolVersion"] == 2

    @pytest.mark.unit
    def test_initialize_no_client_capabilities(self) -> None:
        req = InitializeRequest(protocol_version=2)
        dumped = req.model_dump(by_alias=True, exclude_none=True)
        assert "clientCapabilities" not in dumped
        assert "clientInfo" not in dumped


class TestInitializeResponse:
    """Verify v2 response uses unified capabilities + info."""

    @pytest.mark.unit
    def test_response_no_agent_capabilities(self) -> None:
        resp = InitializeResponse(protocol_version=2)
        dumped = resp.model_dump(by_alias=True, exclude_none=True)
        assert "agentCapabilities" not in dumped
        assert "agentInfo" not in dumped


class TestAuthMethods:
    """Verify v2 auth/login and auth/logout method names."""

    @pytest.mark.unit
    def test_auth_login_method_name(self) -> None:
        assert ClientMethod.AUTH_LOGIN.value == "auth/login"

    @pytest.mark.unit
    def test_auth_logout_method_name(self) -> None:
        assert ClientMethod.AUTH_LOGOUT.value == "auth/logout"

    @pytest.mark.unit
    def test_login_auth_request(self) -> None:
        req = LoginAuthRequest(method_id="oauth")
        dumped = req.model_dump(by_alias=True, exclude_none=True)
        assert dumped["methodId"] == "oauth"

    @pytest.mark.unit
    def test_logout_auth_request(self) -> None:
        req = LogoutAuthRequest()
        dumped = req.model_dump(by_alias=True, exclude_none=True)
        assert "methodId" not in dumped


class TestPromptResponse:
    """Verify v2 prompt response is empty."""

    @pytest.mark.unit
    def test_prompt_response_empty(self) -> None:
        resp = PromptResponse()
        dumped = resp.model_dump(by_alias=True, exclude_none=True)
        assert "stopReason" not in dumped
