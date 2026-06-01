"""Tests for ACP capabilities schema."""

from __future__ import annotations

import pytest

from acp.schema.capabilities import AgentCapabilities, McpCapabilities


class TestAgentCapabilities:
    """Test suite for AgentCapabilities schema."""

    def test_default_load_session(self):
        """Default load_session should be False."""
        caps = AgentCapabilities()
        assert caps.load_session is False

    def test_default_mcp_capabilities(self):
        """Default mcp_capabilities should be None."""
        caps = AgentCapabilities()
        assert caps.mcp_capabilities is None

    def test_default_prompt_capabilities(self):
        """Default prompt_capabilities should be None."""
        caps = AgentCapabilities()
        assert caps.prompt_capabilities is None

    def test_default_session_capabilities(self):
        """Default session_capabilities should be None."""
        caps = AgentCapabilities()
        assert caps.session_capabilities is None

    def test_default_providers_capabilities(self):
        """Default providers should be None."""
        caps = AgentCapabilities()
        assert caps.providers is None

    def test_create_method_with_all_capabilities(self):
        """create() method should set all capabilities correctly."""
        caps = AgentCapabilities.create(
            load_session=True,
            http_mcp_servers=True,
            sse_mcp_servers=True,
            audio_prompts=True,
            embedded_context_prompts=True,
            image_prompts=True,
            list_sessions=True,
            resume_session=True,
            close_session=True,
            fork_session=True,
            providers=True,
        )
        assert caps.load_session is True
        assert caps.mcp_capabilities is not None
        assert caps.mcp_capabilities.http is True
        assert caps.mcp_capabilities.sse is True
        assert caps.prompt_capabilities is not None
        assert caps.prompt_capabilities.audio is True
        assert caps.prompt_capabilities.embedded_context is True
        assert caps.prompt_capabilities.image is True
        assert caps.session_capabilities is not None
        assert caps.session_capabilities.list is not None
        assert caps.session_capabilities.resume is not None
        assert caps.session_capabilities.close is not None
        assert caps.session_capabilities.fork is not None
        assert caps.providers is not None

    def test_create_method_defaults(self):
        """create() method should use correct defaults."""
        caps = AgentCapabilities.create()
        assert caps.load_session is False
        assert caps.mcp_capabilities is not None
        assert caps.mcp_capabilities.http is False
        assert caps.mcp_capabilities.sse is False
        assert caps.prompt_capabilities is not None
        assert caps.prompt_capabilities.audio is False
        assert caps.prompt_capabilities.embedded_context is False
        assert caps.prompt_capabilities.image is False

    def test_json_serialization(self):
        """JSON serialization should not include slash_commands."""
        caps = AgentCapabilities()
        json_data = caps.model_dump(mode="json")
        assert "slash_commands" not in json_data

    def test_json_deserialization_without_slash_commands(self):
        """Backward compatibility: old JSON without slash_commands works."""
        json_data = {
            "load_session": False,
            "mcp_capabilities": {"http": False, "sse": False},
            "prompt_capabilities": {"audio": False, "embedded_context": False, "image": False},
            "session_capabilities": {},
        }
        caps = AgentCapabilities.model_validate(json_data)
        assert caps.load_session is False
        assert caps.mcp_capabilities is not None

    def test_json_deserialization_with_slash_commands_ignored(self):
        """Backward compatibility: old JSON with slash_commands is ignored safely.

        Pydantic ignores extra fields by default, so old JSON containing
        slash_commands should deserialize without errors.
        """
        json_data = {
            "load_session": False,
            "slash_commands": [
                {"name": "cmd1", "description": "Command 1"},
            ],
        }
        caps = AgentCapabilities.model_validate(json_data)
        assert caps.load_session is False
        # slash_commands is not a field on the model, so it's ignored
        assert not hasattr(caps, "slash_commands")


# =============================================================================
# McpCapabilities.acp field tests (RFC-0033)
# =============================================================================


@pytest.mark.unit
def test_mcp_capabilities_acp_defaults_to_false() -> None:
    """McpCapabilities.acp should default to False."""
    caps = McpCapabilities()
    assert caps.acp is False


@pytest.mark.unit
def test_mcp_capabilities_acp_can_be_true() -> None:
    """McpCapabilities.acp can be set to True."""
    caps = McpCapabilities(acp=True)
    assert caps.acp is True


@pytest.mark.unit
def test_mcp_capabilities_acp_json_serialization() -> None:
    """McpCapabilities should include acp field in JSON serialization."""
    caps = McpCapabilities(http=True, sse=True, acp=True)
    json_data = caps.model_dump(mode="json")
    assert json_data["acp"] is True
    assert json_data["http"] is True
    assert json_data["sse"] is True


@pytest.mark.unit
def test_mcp_capabilities_acp_json_deserialization() -> None:
    """McpCapabilities should deserialize acp field from JSON."""
    json_data = {"http": False, "sse": False, "acp": True}
    caps = McpCapabilities.model_validate(json_data)
    assert caps.acp is True
    assert caps.http is False
    assert caps.sse is False


@pytest.mark.unit
def test_mcp_capabilities_round_trip() -> None:
    """McpCapabilities should survive JSON serialization round-trip."""
    original = McpCapabilities(http=True, sse=False, acp=True)
    json_data = original.model_dump(mode="json")
    restored = McpCapabilities.model_validate(json_data)
    assert restored.http is original.http
    assert restored.sse is original.sse
    assert restored.acp is original.acp


# =============================================================================
# AgentCapabilities.create with acp_mcp_servers tests (RFC-0033)
# =============================================================================


@pytest.mark.unit
def test_agent_capabilities_create_with_acp_mcp_servers() -> None:
    """AgentCapabilities.create(acp_mcp_servers=True) should set mcp_capabilities.acp."""
    caps = AgentCapabilities.create(acp_mcp_servers=True)
    assert caps.mcp_capabilities is not None
    assert caps.mcp_capabilities.acp is True


@pytest.mark.unit
def test_agent_capabilities_create_without_acp_mcp_servers_defaults_false() -> None:
    """AgentCapabilities.create() should default acp_mcp_servers to False."""
    caps = AgentCapabilities.create()
    assert caps.mcp_capabilities is not None
    assert caps.mcp_capabilities.acp is False


@pytest.mark.unit
def test_agent_capabilities_create_with_all_mcp_server_types() -> None:
    """AgentCapabilities.create should support all MCP server types together."""
    caps = AgentCapabilities.create(
        http_mcp_servers=True,
        sse_mcp_servers=True,
        acp_mcp_servers=True,
    )
    assert caps.mcp_capabilities is not None
    assert caps.mcp_capabilities.http is True
    assert caps.mcp_capabilities.sse is True
    assert caps.mcp_capabilities.acp is True
