"""Tests for ACP client request schemas."""

from __future__ import annotations

import pytest

from acp.schema.client_requests import (
    DisableProvidersRequest,
    ListProvidersRequest,
    SetProvidersRequest,
)


class TestListProvidersRequest:
    """Test suite for ListProvidersRequest schema."""

    def test_basic_creation(self):
        """ListProvidersRequest should create with no fields."""
        req = ListProvidersRequest()
        json_data = req.model_dump(mode="json")
        # Request base class has field_meta, ignore it
        assert json_data.get("field_meta") is None


class TestSetProvidersRequest:
    """Test suite for SetProvidersRequest schema."""

    def test_basic_creation(self):
        """SetProvidersRequest should create with required fields."""
        req = SetProvidersRequest(
            id="openai",
            api_type="openai",
            base_url="https://api.openai.com",
        )
        assert req.id == "openai"
        assert req.api_type == "openai"
        assert req.base_url == "https://api.openai.com"
        assert req.headers is None

    def test_with_headers(self):
        """SetProvidersRequest should accept optional headers."""
        req = SetProvidersRequest(
            id="custom",
            api_type="openai-compatible",
            base_url="https://custom.api.com",
            headers={"Authorization": "Bearer token"},
        )
        assert req.headers == {"Authorization": "Bearer token"}

    def test_json_serialization(self):
        """SetProvidersRequest should serialize to JSON correctly."""
        req = SetProvidersRequest(
            id="anthropic",
            api_type="anthropic",
            base_url="https://api.anthropic.com",
        )
        json_data = req.model_dump(mode="json")
        assert json_data["id"] == "anthropic"
        assert json_data["api_type"] == "anthropic"
        assert json_data["base_url"] == "https://api.anthropic.com"


class TestDisableProvidersRequest:
    """Test suite for DisableProvidersRequest schema."""

    def test_basic_creation(self):
        """DisableProvidersRequest should create with required fields."""
        req = DisableProvidersRequest(id="openai")
        assert req.id == "openai"

    def test_json_serialization(self):
        """DisableProvidersRequest should serialize to JSON correctly."""
        req = DisableProvidersRequest(id="test-provider")
        json_data = req.model_dump(mode="json")
        assert json_data["id"] == "test-provider"
