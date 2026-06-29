"""Tests for v1+v2 dual-version coexistence."""

from __future__ import annotations

import pytest

from agentpool_server.acp_server.shared.version_negotiator import VersionNegotiator
from agentpool_server.acp_server.v1 import (
    ACPEventConverter as V1Converter,
    AgentPoolACPAgent as V1Agent,
)
from agentpool_server.acp_server.v2.acp_agent import AgentPoolACPAgentV2
from agentpool_server.acp_server.v2.event_converter import ACPEventConverterV2
from agentpool_server.acp_server.v2.prompt_lifecycle import PromptLifecycleManager


class TestDualVersionCoexistence:
    """Verify v1 and v2 code can coexist without conflicts."""

    @pytest.mark.unit
    def test_v1_and_v2_agents_are_different_classes(self) -> None:
        assert V1Agent is not AgentPoolACPAgentV2
        assert V1Agent.PROTOCOL_VERSION == 1
        assert AgentPoolACPAgentV2.PROTOCOL_VERSION == 2

    @pytest.mark.unit
    def test_v1_and_v2_converters_are_different_classes(self) -> None:
        assert V1Converter is not ACPEventConverterV2

    @pytest.mark.unit
    def test_version_negotiator_routes_v1(self) -> None:
        assert VersionNegotiator.negotiate(1) == 1

    @pytest.mark.unit
    def test_version_negotiator_routes_v2(self) -> None:
        assert VersionNegotiator.negotiate(2) == 2

    @pytest.mark.unit
    def test_v2_has_no_session_modes(self) -> None:
        assert not hasattr(AgentPoolACPAgentV2, "set_session_mode")

    @pytest.mark.unit
    def test_v2_has_auth_methods(self) -> None:
        assert hasattr(AgentPoolACPAgentV2, "auth_login")
        assert hasattr(AgentPoolACPAgentV2, "auth_logout")

    @pytest.mark.unit
    def test_v1_still_has_set_session_mode(self) -> None:
        assert hasattr(V1Agent, "set_session_mode")

    @pytest.mark.unit
    def test_v1_still_has_authenticate(self) -> None:
        assert hasattr(V1Agent, "authenticate")

    @pytest.mark.unit
    def test_v2_prompt_lifecycle_manager_exists(self) -> None:
        mgr = PromptLifecycleManager()
        assert mgr.state == "idle"
