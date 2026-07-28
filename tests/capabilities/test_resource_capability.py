"""Tests for ResourceCapability — unified resource access via 5 agent-facing tools."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from pydantic_ai import BinaryContent, ToolReturn
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset
import pytest

from agentpool.capabilities.agent_context import AgentContext
from agentpool.capabilities.extension_registry import ExtensionRegistry, Scope, ScopeLevel
from agentpool.capabilities.resource_capability import ResourceCapability
from agentpool.capabilities.resource_protocols import (
    BlobResourceContent,
    CompletionArgument,
    CompletionResult,
    ResourceEntry,
    ResourceTemplateEntry,
    SkillEntry,
    TextResourceContent,
)
from agentpool.host.context import RunScope


if TYPE_CHECKING:
    from collections.abc import Sequence


pytestmark = pytest.mark.unit


# =============================================================================
# Fake providers
# =============================================================================


class FakeResourceAccess:
    """Minimal ResourceAccess implementation for testing."""

    def __init__(
        self,
        *,
        resources: list[ResourceEntry] | None = None,
        read_contents: list[TextResourceContent | BlobResourceContent] | None = None,
        exists_uris: set[str] | None = None,
        read_exception: Exception | None = None,
    ) -> None:
        self._resources = resources or []
        self._read_contents = read_contents
        self._exists_uris = exists_uris or set()
        self._read_exception = read_exception

    async def list_resources(self) -> Sequence[ResourceEntry]:
        return list(self._resources)

    async def read_resource(
        self, uri: str
    ) -> list[TextResourceContent | BlobResourceContent] | None:
        if self._read_exception is not None:
            raise self._read_exception
        if self._read_contents is None:
            return None
        return list(self._read_contents)

    async def resource_exists(self, uri: str) -> bool:
        return uri in self._exists_uris


class FakeSkillResource:
    """Minimal SkillResource implementation for testing."""

    def __init__(
        self,
        *,
        skills: list[SkillEntry] | None = None,
        read_content: str | None = None,
        exists_names: set[str] | None = None,
    ) -> None:
        self._skills = skills or []
        self._read_content = read_content
        self._exists_names = exists_names or set()

    async def list_skills(self) -> Sequence[SkillEntry]:
        return list(self._skills)

    async def read_skill(self, name: str) -> str | None:
        if name in self._exists_names:
            return self._read_content
        return None

    async def skill_exists(self, name: str) -> bool:
        return name in self._exists_names


class FakeResourceTemplateAccess:
    """Minimal ResourceTemplateAccess implementation for testing."""

    def __init__(
        self,
        *,
        templates: list[ResourceTemplateEntry] | None = None,
        completion_result: CompletionResult | None = None,
        raise_not_implemented: bool = False,
    ) -> None:
        self._templates = templates or []
        self._completion_result = completion_result
        self._raise_not_implemented = raise_not_implemented

    async def list_resource_templates(self) -> Sequence[ResourceTemplateEntry]:
        return list(self._templates)

    async def complete_resource_template(
        self,
        uri_template: str,
        argument: CompletionArgument,
        context: dict[str, str] | None = None,
    ) -> CompletionResult:
        if self._raise_not_implemented:
            raise NotImplementedError
        if self._completion_result is not None:
            return self._completion_result
        return CompletionResult(values=[])


# =============================================================================
# Helpers
# =============================================================================


def _make_agent_context(
    registry: ExtensionRegistry | None = None,
) -> AgentContext:
    """Build an AgentContext with test doubles."""
    agent_registry = MagicMock()
    delegation = MagicMock()
    session = MagicMock()
    session.session_id = "test-session-001"
    host = MagicMock()
    return AgentContext(
        agent_registry=agent_registry,
        delegation=delegation,
        session=session,
        scope=RunScope(),
        host=host,
        extension_registry=registry,
    )


def _make_ctx(agent_ctx: AgentContext) -> Any:
    """Create a RunContext-like object with AgentContext as deps."""
    ctx = MagicMock()
    ctx.deps = agent_ctx
    return ctx


def _make_registry_with_caps(
    *caps: Any,
) -> ExtensionRegistry:
    """Build an ExtensionRegistry and register caps at AGENT scope."""
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.AGENT, session_id="test-session-001")
    for cap in caps:
        registry.register(cap, scope)
    return registry


# =============================================================================
# Tests
# =============================================================================


def test_is_abstract_capability() -> None:
    """ResourceCapability is an instance of AbstractCapability."""
    cap = ResourceCapability()
    assert isinstance(cap, AbstractCapability)


def test_get_toolset_returns_function_toolset() -> None:
    """get_toolset() returns a FunctionToolset with 5 tools."""
    cap = ResourceCapability()
    toolset = cap.get_toolset()
    assert toolset is not None
    assert isinstance(toolset, FunctionToolset)
    assert "list_resources" in toolset.tools
    assert "read_resource" in toolset.tools
    assert "resource_exists" in toolset.tools
    assert "list_resource_templates" in toolset.tools
    assert "complete_resource_template" in toolset.tools


def test_name_property() -> None:
    """Name property returns 'resource_capability'."""
    cap = ResourceCapability()
    assert cap.name == "resource_capability"


def test_get_instructions_returns_description() -> None:
    """get_instructions() returns a non-None description mentioning all tools."""
    cap = ResourceCapability()
    instructions = cap.get_instructions()
    assert instructions is not None
    assert "list_resources" in instructions
    assert "read_resource" in instructions
    assert "resource_exists" in instructions
    assert "list_resource_templates" in instructions
    assert "complete_resource_template" in instructions
    assert "skill://" in instructions
    assert "mcp://" in instructions


async def test_stateless_lifecycle() -> None:
    """__aenter__ returns self, __aexit__ is a no-op."""
    cap = ResourceCapability[Any]()
    result = await cap.__aenter__()
    assert result is cap
    exit_result = await cap.__aexit__(None, None, None)
    assert exit_result is None


async def test_list_resources_with_providers() -> None:
    """list_resources aggregates resources from ResourceAccess and SkillResource providers."""
    ra = FakeResourceAccess(
        resources=[
            ResourceEntry(
                uri="mcp://server/file.txt",
                name="file.txt",
                description="A text file",
                mime_type="text/plain",
            ),
        ],
    )
    sr = FakeSkillResource(
        skills=[
            SkillEntry(
                name="ponytail",
                description="Lazy senior dev mode",
                uri="skill://ponytail/SKILL.md",
                source="local",
            ),
        ],
    )
    registry = _make_registry_with_caps(ra, sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resources(ctx)

    assert "FakeResourceAccess" in result
    assert "mcp://server/file.txt" in result
    assert "file.txt" in result
    assert "FakeSkillResource" in result
    assert "ponytail" in result


async def test_list_resources_no_registry() -> None:
    """list_resources returns 'No resources available.' when extension_registry is None."""
    agent_ctx = _make_agent_context(registry=None)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resources(ctx)

    assert result == "No resources available."


async def test_read_resource_skill_uri() -> None:
    """read_resource routes skill:// URIs to SkillResource providers."""
    sr = FakeSkillResource(
        skills=[SkillEntry(name="ponytail", uri="skill://ponytail/SKILL.md")],
        read_content="# Ponytail\nLazy dev mode.",
        exists_names={"ponytail"},
    )
    registry = _make_registry_with_caps(sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "skill://ponytail/SKILL.md")

    assert isinstance(result, ToolReturn)
    assert result.content is not None
    assert "# Ponytail" in result.return_value


async def test_read_resource_mcp_uri() -> None:
    """read_resource routes non-skill URIs to ResourceAccess providers with TextResourceContent."""
    ra = FakeResourceAccess(
        read_contents=[
            TextResourceContent(
                uri="mcp://server/file.txt",
                mime_type="text/plain",
                text="Hello, world!",
            ),
        ],
    )
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "mcp://server/file.txt")

    assert isinstance(result, ToolReturn)
    assert result.content is not None
    assert "Hello, world!" in result.return_value


async def test_read_resource_blob() -> None:
    """read_resource converts BlobResourceContent to BinaryContent."""
    raw_data = b"\x89PNG\r\n\x1a\n"
    encoded = base64.b64encode(raw_data).decode("ascii")
    ra = FakeResourceAccess(
        read_contents=[
            BlobResourceContent(
                uri="mcp://server/image.png",
                mime_type="image/png",
                blob=encoded,
            ),
        ],
    )
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "mcp://server/image.png")

    assert isinstance(result, ToolReturn)
    assert result.content is not None
    binary = result.content[0]
    assert isinstance(binary, BinaryContent)
    assert binary.data == raw_data
    assert binary.media_type == "image/png"


async def test_read_resource_not_found() -> None:
    """read_resource returns 'Resource not found' when no provider has the resource."""
    ra = FakeResourceAccess(read_contents=None)
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "mcp://server/missing.txt")

    assert isinstance(result, ToolReturn)
    assert result.return_value == "Resource not found: mcp://server/missing.txt"


async def test_read_resource_no_registry() -> None:
    """read_resource returns 'Resource not found' when extension_registry is None."""
    agent_ctx = _make_agent_context(registry=None)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "mcp://server/file.txt")

    assert isinstance(result, ToolReturn)
    assert result.return_value == "Resource not found: mcp://server/file.txt"


async def test_resource_exists_true() -> None:
    """resource_exists returns True when a provider has the resource."""
    ra = FakeResourceAccess(exists_uris={"mcp://server/file.txt"})
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.resource_exists(ctx, "mcp://server/file.txt")

    assert result is True


async def test_resource_exists_false() -> None:
    """resource_exists returns False when no provider has the resource."""
    ra = FakeResourceAccess(exists_uris=set())
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.resource_exists(ctx, "mcp://server/missing.txt")

    assert result is False


async def test_resource_exists_skill_uri() -> None:
    """resource_exists routes skill:// URIs to SkillResource providers."""
    sr = FakeSkillResource(exists_names={"ponytail"})
    registry = _make_registry_with_caps(sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.resource_exists(ctx, "skill://ponytail/SKILL.md")

    assert result is True


async def test_resource_exists_no_registry() -> None:
    """resource_exists returns False when extension_registry is None."""
    agent_ctx = _make_agent_context(registry=None)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.resource_exists(ctx, "mcp://server/file.txt")

    assert result is False


async def test_list_resource_templates() -> None:
    """list_resource_templates formats template table output."""
    rta = FakeResourceTemplateAccess(
        templates=[
            ResourceTemplateEntry(
                uri_template="file:///{path}",
                name="file_template",
                title="File Template",
                description="Access files by path",
                mime_type="text/plain",
            ),
        ],
    )
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resource_templates(ctx)

    assert "FakeResourceTemplateAccess" in result
    assert "file:///{path}" in result
    assert "file_template" in result
    assert "File Template" in result


async def test_list_resource_templates_no_registry() -> None:
    """list_resource_templates returns graceful empty when extension_registry is None."""
    agent_ctx = _make_agent_context(registry=None)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resource_templates(ctx)

    assert result == "No resource templates available."


async def test_list_resource_templates_empty() -> None:
    """list_resource_templates returns graceful empty when no templates exist."""
    rta = FakeResourceTemplateAccess(templates=[])
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.list_resource_templates(ctx)

    assert result == "No resource templates available."


async def test_complete_resource_template() -> None:
    """complete_resource_template returns formatted completion suggestions."""
    rta = FakeResourceTemplateAccess(
        templates=[
            ResourceTemplateEntry(uri_template="file:///{path}"),
        ],
        completion_result=CompletionResult(
            values=["file1.txt", "file2.txt"],
            total=2,
            has_more=False,
        ),
    )
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.complete_resource_template(ctx, "file:///{path}", "path", "file")

    assert "file1.txt" in result
    assert "file2.txt" in result


async def test_complete_resource_template_not_supported() -> None:
    """complete_resource_template handles NotImplementedError gracefully."""
    rta = FakeResourceTemplateAccess(
        templates=[
            ResourceTemplateEntry(uri_template="file:///{path}"),
        ],
        raise_not_implemented=True,
    )
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.complete_resource_template(ctx, "file:///{path}", "path", "file")

    assert "Completion not supported for template: file:///{path}" in result


async def test_complete_resource_template_no_matching_template() -> None:
    """complete_resource_template returns 'not supported' when no matching template found."""
    rta = FakeResourceTemplateAccess(
        templates=[
            ResourceTemplateEntry(uri_template="file:///{path}"),
        ],
    )
    registry = _make_registry_with_caps(rta)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.complete_resource_template(ctx, "unknown://template", "param", "val")

    assert "Completion not supported for template: unknown://template" in result


async def test_complete_resource_template_no_registry() -> None:
    """complete_resource_template returns graceful empty when extension_registry is None."""
    agent_ctx = _make_agent_context(registry=None)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.complete_resource_template(ctx, "file:///{path}", "path", "file")

    assert "No resource template providers available." in result


def test_toolset_id_customizable() -> None:
    """Toolset ID can be customized via constructor."""
    cap = ResourceCapability(toolset_id="custom_resources")
    toolset = cap.get_toolset()
    assert isinstance(toolset, FunctionToolset)
    assert toolset.id == "custom_resources"


def test_default_toolset_id() -> None:
    """Default toolset ID is 'resource_access'."""
    cap = ResourceCapability()
    toolset = cap.get_toolset()
    assert isinstance(toolset, FunctionToolset)
    assert toolset.id == "resource_access"


async def test_read_resource_skill_not_found() -> None:
    """read_resource returns 'Resource not found' for missing skill."""
    sr = FakeSkillResource(exists_names=set())
    registry = _make_registry_with_caps(sr)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "skill://nonexistent/SKILL.md")

    assert isinstance(result, ToolReturn)
    assert "Resource not found" in result.return_value


async def test_read_resource_provider_exception() -> None:
    """read_resource skips providers that raise exceptions."""
    ra_good = FakeResourceAccess(
        read_contents=[
            TextResourceContent(uri="mcp://server/file.txt", text="found!"),
        ],
    )
    ra_bad = FakeResourceAccess(read_exception=RuntimeError("connection failed"))
    registry = _make_registry_with_caps(ra_bad, ra_good)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "mcp://server/file.txt")

    assert isinstance(result, ToolReturn)
    assert result.content is not None
    assert "found!" in result.return_value


async def test_read_resource_blob_default_mime_type() -> None:
    """read_resource uses 'application/octet-stream' when mime_type is None."""
    raw_data = b"\x00\x01\x02"
    encoded = base64.b64encode(raw_data).decode("ascii")
    ra = FakeResourceAccess(
        read_contents=[
            BlobResourceContent(
                uri="mcp://server/data.bin",
                mime_type=None,
                blob=encoded,
            ),
        ],
    )
    registry = _make_registry_with_caps(ra)
    agent_ctx = _make_agent_context(registry)
    ctx = _make_ctx(agent_ctx)

    cap = ResourceCapability()
    result = await cap.read_resource(ctx, "mcp://server/data.bin")

    assert isinstance(result, ToolReturn)
    assert result.content is not None
    binary = result.content[0]
    assert isinstance(binary, BinaryContent)
    assert binary.media_type == "application/octet-stream"


def test_extract_skill_name() -> None:
    """_extract_skill_name takes the first path segment from a skill:// URI."""
    assert ResourceCapability._extract_skill_name("skill://ponytail/SKILL.md") == "ponytail"
    assert ResourceCapability._extract_skill_name("skill://my-skill") == "my-skill"
    assert ResourceCapability._extract_skill_name("skill://a/b/c") == "a"
    assert ResourceCapability._extract_skill_name("skill://") == ""
