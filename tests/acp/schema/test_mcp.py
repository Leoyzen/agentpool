"""Tests for ACP MCP schema definitions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from acp.schema.mcp import (
    AcpMcpServer,
    BaseMcpServer,
    HttpMcpServer,
    SseMcpServer,
    StdioMcpServer,
)


@pytest.mark.unit
def test_acp_mcp_server_creation() -> None:
    """AcpMcpServer should be created with name and id."""
    server = AcpMcpServer(name="test-server", id="server-123")
    assert server.name == "test-server"
    assert server.id == "server-123"


@pytest.mark.unit
def test_acp_mcp_server_default_type() -> None:
    """AcpMcpServer should have default type 'acp'."""
    server = AcpMcpServer(name="test-server", id="server-123")
    assert server.type == "acp"


@pytest.mark.unit
def test_acp_mcp_server_is_base_mcp_server() -> None:
    """AcpMcpServer should be an instance of BaseMcpServer."""
    server = AcpMcpServer(name="test-server", id="server-123")
    assert isinstance(server, BaseMcpServer)


@pytest.mark.unit
def test_acp_mcp_server_in_union() -> None:
    """AcpMcpServer should be a valid McpServer variant."""
    server = AcpMcpServer(name="test-server", id="server-123")
    assert isinstance(server, (HttpMcpServer, SseMcpServer, StdioMcpServer, AcpMcpServer))


@pytest.mark.unit
def test_acp_mcp_server_json_serialization() -> None:
    """AcpMcpServer should serialize to JSON with correct fields."""
    server = AcpMcpServer(name="test-server", id="server-123")
    json_data = server.model_dump(mode="json")
    assert json_data["name"] == "test-server"
    assert json_data["id"] == "server-123"
    assert json_data["type"] == "acp"


@pytest.mark.unit
def test_acp_mcp_server_json_deserialization() -> None:
    """AcpMcpServer should deserialize from JSON correctly."""
    json_data = {"name": "test-server", "type": "acp", "id": "server-123"}
    server = AcpMcpServer.model_validate(json_data)
    assert server.name == "test-server"
    assert server.id == "server-123"
    assert server.type == "acp"


@pytest.mark.unit
def test_acp_mcp_server_round_trip() -> None:
    """AcpMcpServer should survive JSON serialization round-trip."""
    original = AcpMcpServer(name="test-server", id="server-123")
    json_data = original.model_dump(mode="json")
    restored = AcpMcpServer.model_validate(json_data)
    assert restored.name == original.name
    assert restored.id == original.id
    assert restored.type == original.type


@pytest.mark.unit
def test_acp_mcp_server_invalid_type_rejected() -> None:
    """AcpMcpServer should reject invalid type value during deserialization."""
    json_data = {"name": "test-server", "type": "http", "id": "server-123"}
    with pytest.raises(ValidationError):
        AcpMcpServer.model_validate(json_data)


@pytest.mark.unit
def test_acp_mcp_server_missing_id_rejected() -> None:
    """AcpMcpServer should reject JSON missing required id field."""
    json_data = {"name": "test-server", "type": "acp"}
    with pytest.raises(ValidationError):
        AcpMcpServer.model_validate(json_data)


@pytest.mark.unit
def test_acp_mcp_server_missing_name_rejected() -> None:
    """AcpMcpServer should reject JSON missing required name field."""
    json_data = {"type": "acp", "id": "server-123"}
    with pytest.raises(ValidationError):
        AcpMcpServer.model_validate(json_data)
