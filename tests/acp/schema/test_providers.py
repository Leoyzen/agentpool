"""Tests for ACP providers schema."""

from __future__ import annotations

import pytest

from acp.schema.providers import (
    ProviderCurrentConfig,
    ProviderInfo,
    ProvidersCapabilities,
    ProviderStatus,
)


class TestProviderCurrentConfig:
    """Test suite for ProviderCurrentConfig schema."""

    def test_basic_creation(self):
        """ProviderCurrentConfig should create with required fields."""
        config = ProviderCurrentConfig(api_type="openai", base_url="https://api.openai.com")
        assert config.api_type == "openai"
        assert config.base_url == "https://api.openai.com"


class TestProviderInfo:
    """Test suite for ProviderInfo schema."""

    def test_basic_creation(self):
        """ProviderInfo should create with required fields."""
        info = ProviderInfo(
            id="openai",
            supported=["openai"],
            required=True,
        )
        assert info.id == "openai"
        assert info.supported == ["openai"]
        assert info.required is True
        assert info.current is None

    def test_with_current_config(self):
        """ProviderInfo should accept current config."""
        current = ProviderCurrentConfig(api_type="openai", base_url="https://api.openai.com")
        info = ProviderInfo(
            id="openai",
            supported=["openai"],
            required=False,
            current=current,
        )
        assert info.current is not None
        assert info.current.api_type == "openai"

    def test_json_serialization(self):
        """ProviderInfo should serialize to JSON correctly."""
        info = ProviderInfo(
            id="test",
            supported=["openai", "ollama"],
            required=True,
        )
        json_data = info.model_dump(mode="json")
        assert json_data["id"] == "test"
        assert json_data["supported"] == ["openai", "ollama"]
        assert json_data["required"] is True
        assert json_data["current"] is None


class TestProvidersCapabilities:
    """Test suite for ProvidersCapabilities schema."""

    def test_empty_creation(self):
        """ProvidersCapabilities should create empty."""
        caps = ProvidersCapabilities()
        json_data = caps.model_dump(mode="json")
        assert json_data == {}


class TestProviderStatus:
    """Test suite for ProviderStatus class."""

    def test_values(self):
        """ProviderStatus should have expected values."""
        assert ProviderStatus.enabled == "enabled"
        assert ProviderStatus.disabled == "disabled"
