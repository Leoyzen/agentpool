"""Tests for ACP agent response schemas."""

from __future__ import annotations

import pytest

from acp.schema.agent_responses import (
    DisableProvidersResponse,
    ListProvidersResponse,
    SetProvidersResponse,
)
from acp.schema.providers import ProviderCurrentConfig, ProviderInfo

pytestmark = pytest.mark.unit


class TestListProvidersResponse:
    """Test suite for ListProvidersResponse schema."""

    def test_empty_creation(self):
        """ListProvidersResponse should create with empty providers list."""
        resp = ListProvidersResponse()
        assert resp.providers == []

    def test_with_providers(self):
        """ListProvidersResponse should accept a list of providers."""
        provider = ProviderInfo(
            id="openai",
            supported=["openai"],
            required=True,
            current=ProviderCurrentConfig(api_type="openai", base_url="https://api.openai.com"),
        )
        resp = ListProvidersResponse(providers=[provider])
        assert len(resp.providers) == 1
        assert resp.providers[0].id == "openai"

    def test_json_serialization(self):
        """ListProvidersResponse should serialize to JSON correctly."""
        provider = ProviderInfo(id="test", supported=["openai"], required=False)
        resp = ListProvidersResponse(providers=[provider])
        json_data = resp.model_dump(mode="json")
        assert len(json_data["providers"]) == 1
        assert json_data["providers"][0]["id"] == "test"


class TestSetProvidersResponse:
    """Test suite for SetProvidersResponse schema."""

    def test_empty_creation(self):
        """SetProvidersResponse should create with no fields."""
        resp = SetProvidersResponse()
        json_data = resp.model_dump(mode="json")
        # Response base class has field_meta, ignore it
        assert json_data.get("field_meta") is None


class TestDisableProvidersResponse:
    """Test suite for DisableProvidersResponse schema."""

    def test_empty_creation(self):
        """DisableProvidersResponse should create with no fields."""
        resp = DisableProvidersResponse()
        json_data = resp.model_dump(mode="json")
        # Response base class has field_meta, ignore it
        assert json_data.get("field_meta") is None
