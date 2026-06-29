"""Tests for ACP version negotiation."""

from __future__ import annotations

import pytest
from acp.exceptions import RequestError

from agentpool_server.acp_server.shared.version_negotiator import VersionNegotiator


class TestVersionNegotiation:
    """Verify version negotiation routes to correct protocol path."""

    @pytest.mark.unit
    def test_version_1_returns_1(self) -> None:
        """v1 client gets v1 path."""
        result = VersionNegotiator.negotiate(1)
        assert result == 1

    @pytest.mark.unit
    def test_version_2_returns_2(self) -> None:
        """v2 client gets v2 path."""
        result = VersionNegotiator.negotiate(2)
        assert result == 2

    @pytest.mark.unit
    def test_version_3_returns_2(self) -> None:
        """Future version 3 negotiates down to v2 (highest supported)."""
        result = VersionNegotiator.negotiate(3)
        assert result == 2

    @pytest.mark.unit
    def test_version_0_raises_error(self) -> None:
        """Unsupported version 0 raises RequestError."""
        with pytest.raises(RequestError, match="Unsupported protocol version: 0"):
            VersionNegotiator.negotiate(0)

    @pytest.mark.unit
    def test_negative_version_raises_error(self) -> None:
        """Negative version raises RequestError."""
        with pytest.raises(RequestError, match="Unsupported protocol version"):
            VersionNegotiator.negotiate(-1)

    @pytest.mark.unit
    def test_is_supported_v1(self) -> None:
        """is_supported returns True for v1."""
        assert VersionNegotiator.is_supported(1) is True

    @pytest.mark.unit
    def test_is_supported_v2(self) -> None:
        """is_supported returns True for v2."""
        assert VersionNegotiator.is_supported(2) is True

    @pytest.mark.unit
    def test_is_supported_v0(self) -> None:
        """is_supported returns False for v0."""
        assert VersionNegotiator.is_supported(0) is False

    @pytest.mark.unit
    def test_is_supported_v99(self) -> None:
        """is_supported returns False for unknown v99."""
        assert VersionNegotiator.is_supported(99) is False
