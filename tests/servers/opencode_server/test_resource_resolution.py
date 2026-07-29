"""Tests for resource resolution utility and converter integration.

Covers the shared resource resolution utility (L1 unit tests) and the OpenCode
converter's integration of that utility when handling ``FilePartInput`` with
``ResourceSource`` (L2 integration tests).

The tests are written against the target API where:
- ``resolve_resource_content()`` lives in ``agentpool.capabilities.resource_resolver``
- ``_resolve_resource()`` in the converter delegates to ``resolve_resource_content()``
  by filtering ``agent._all_capabilities`` via ``isinstance`` checks.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from pydantic_ai import BinaryContent
import pytest

from agentpool.capabilities.resource_protocols import (
    BlobResourceContent,
    ResourceEntry,
    SkillEntry,
    TextResourceContent,
)
from agentpool.capabilities.resource_resolver import resolve_resource_content


if TYPE_CHECKING:
    from collections.abc import Sequence


pytestmark = pytest.mark.unit


# =============================================================================
# Fake capability implementations
# =============================================================================


class FakeResourceAccess:
    """Minimal ``ResourceAccess`` implementation for testing.

    Implements the three async methods required by the ``ResourceAccess``
    protocol: ``list_resources``, ``read_resource``, ``resource_exists``.
    """

    def __init__(
        self,
        read_result: list[TextResourceContent | BlobResourceContent] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._read_result = read_result
        self._raise_exc = raise_exc

    async def list_resources(self) -> Sequence[ResourceEntry]:
        return []

    async def read_resource(
        self, uri: str
    ) -> list[TextResourceContent | BlobResourceContent] | None:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._read_result

    async def resource_exists(self, uri: str) -> bool:
        return self._read_result is not None


class FakeSkillResource:
    """Minimal ``SkillResource`` implementation for testing.

    Implements the three async methods required by the ``SkillResource``
    protocol: ``list_skills``, ``read_skill``, ``skill_exists``.
    """

    def __init__(
        self,
        read_result: str | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._read_result = read_result
        self._raise_exc = raise_exc

    async def list_skills(self) -> Sequence[SkillEntry]:
        return []

    async def read_skill(self, name: str) -> str | None:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._read_result

    async def skill_exists(self, name: str) -> bool:
        return self._read_result is not None


class FakeAgent:
    """Minimal agent with ``host_context`` for integration tests.

    Provides an ``ExtensionRegistry`` with the given capabilities registered
    at POOL scope so ``_resolve_resource`` can find them via the registry.
    """

    def __init__(self, capabilities: list[Any], name: str = "fake-agent") -> None:
        from agentpool.capabilities.extension_registry import ExtensionRegistry, Scope, ScopeLevel

        registry = ExtensionRegistry()
        pool_scope = Scope(level=ScopeLevel.POOL)
        for cap in capabilities:
            registry.register(cap, pool_scope)
        # Lightweight fake host_context — only extension_registry is accessed
        self.name = name
        self._host_context = SimpleNamespace(extension_registry=registry)

    @property
    def host_context(self) -> Any:
        return self._host_context


# =============================================================================
# L1 Unit Tests — resolve_resource_content()
# =============================================================================


async def test_resolve_resource_text_content() -> None:
    """Text resource via ResourceAccess returns XML-wrapped text."""
    cap = FakeResourceAccess(read_result=[TextResourceContent(text="hello", uri="viking://doc.md")])
    result = await resolve_resource_content("viking://doc.md", resource_caps=[cap], skill_caps=[])
    assert result is not None
    assert result == ['<resource uri="viking://doc.md">\nhello\n</resource>']


async def test_resolve_resource_binary_content() -> None:
    """Binary resource via ResourceAccess returns [str, BinaryContent, str]."""
    blob_data = base64.b64encode(b"img").decode()
    cap = FakeResourceAccess(
        read_result=[
            BlobResourceContent(blob=blob_data, mime_type="image/png", uri="viking://img.png")
        ]
    )
    result = await resolve_resource_content("viking://img.png", resource_caps=[cap], skill_caps=[])
    assert result is not None
    assert len(result) == 3
    assert result[0] == '<resource uri="viking://img.png">\n'
    assert isinstance(result[1], BinaryContent)
    assert result[1].data == b"img"
    assert result[1].media_type == "image/png"
    assert result[2] == "\n</resource>"


async def test_resolve_resource_not_found() -> None:
    """No provider returns content → result is None."""
    cap = FakeResourceAccess(read_result=None)
    result = await resolve_resource_content("viking://missing", resource_caps=[cap], skill_caps=[])
    assert result is None


async def test_resolve_resource_read_returns_empty() -> None:
    """``read_resource()`` returns empty list → returns None."""
    cap = FakeResourceAccess(read_result=[])
    result = await resolve_resource_content("viking://empty", resource_caps=[cap], skill_caps=[])
    assert result is None


async def test_resolve_resource_read_raises_exception() -> None:
    """``read_resource()`` raises → logs, continues, returns None."""
    cap = FakeResourceAccess(raise_exc=RuntimeError("connection lost"))
    result = await resolve_resource_content("viking://error", resource_caps=[cap], skill_caps=[])
    assert result is None


async def test_resolve_resource_mixed_text_and_binary() -> None:
    """Both TextResourceContent and BlobResourceContent returned → all items in output."""
    blob_data = base64.b64encode(b"pic").decode()
    cap = FakeResourceAccess(
        read_result=[
            TextResourceContent(text="desc", uri="viking://mixed"),
            BlobResourceContent(blob=blob_data, mime_type="image/png", uri="viking://mixed"),
        ]
    )
    result = await resolve_resource_content("viking://mixed", resource_caps=[cap], skill_caps=[])
    assert result is not None
    # Text item → single XML-wrapped string
    assert result[0] == '<resource uri="viking://mixed">\ndesc\n</resource>'
    # Binary item → [str, BinaryContent, str]
    assert result[1] == '<resource uri="viking://mixed">\n'
    assert isinstance(result[2], BinaryContent)
    assert result[2].data == b"pic"
    assert result[2].media_type == "image/png"
    assert result[3] == "\n</resource>"


async def test_resolve_resource_multiple_providers() -> None:
    """First provider returns None, second returns content → returns content from second."""
    cap1 = FakeResourceAccess(read_result=None)
    cap2 = FakeResourceAccess(read_result=[TextResourceContent(text="found", uri="viking://doc")])
    result = await resolve_resource_content(
        "viking://doc", resource_caps=[cap1, cap2], skill_caps=[]
    )
    assert result is not None
    assert result == ['<resource uri="viking://doc">\nfound\n</resource>']


async def test_resolve_resource_skill_uri() -> None:
    """``skill://`` URI routed to SkillResource, not ResourceAccess."""
    skill_cap = FakeSkillResource(read_result="content")
    # ResourceAccess cap that would fail if called — proves routing skips it
    resource_cap = FakeResourceAccess(raise_exc=AssertionError("should not be called"))
    result = await resolve_resource_content(
        "skill://ponytail/SKILL.md",
        resource_caps=[resource_cap],
        skill_caps=[skill_cap],
    )
    assert result is not None
    assert result == ['<resource uri="skill://ponytail/SKILL.md">\ncontent\n</resource>']


async def test_resolve_resource_xml_wrapper_format() -> None:
    r"""Verify exact XML format for text: ``<resource uri="{uri}">\n{content}\n</resource>``."""
    cap = FakeResourceAccess(read_result=[TextResourceContent(text="hello", uri="viking://doc.md")])
    result = await resolve_resource_content("viking://doc.md", resource_caps=[cap], skill_caps=[])
    assert result is not None
    assert len(result) == 1
    expected = '<resource uri="viking://doc.md">\nhello\n</resource>'
    assert result[0] == expected


async def test_resolve_resource_text_truncation() -> None:
    """Text > max_text_chars → truncated with suffix."""
    long_text = "x" * 15_000
    cap = FakeResourceAccess(read_result=[TextResourceContent(text=long_text, uri="viking://big")])
    result = await resolve_resource_content(
        "viking://big", resource_caps=[cap], skill_caps=[], max_text_chars=10_000
    )
    assert result is not None
    assert len(result) == 1
    wrapped: str = result[0]  # type: ignore[assignment]
    # The XML wrapper contains the truncated text
    assert '<resource uri="viking://big">' in wrapped
    assert "</resource>" in wrapped
    # The body is the first 10_000 chars + suffix
    suffix = f"\n\n... [truncated: {len(long_text)} chars total, showing first 10000]"
    expected_body = long_text[:10_000] + suffix
    assert f'<resource uri="viking://big">\n{expected_body}\n</resource>' == wrapped


# =============================================================================
# L2 Integration Tests — extract_user_prompt_from_parts()
# =============================================================================


async def test_extract_user_prompt_with_resource_source() -> None:
    """FilePartInput with ResourceSource and agent → XML-wrapped text in result."""
    from agentpool_server.opencode_server.converters import extract_user_prompt_from_parts
    from agentpool_server.opencode_server.models import FilePartInput
    from agentpool_server.opencode_server.models.common import TextSpan
    from agentpool_server.opencode_server.models.parts import ResourceSource

    cap = FakeResourceAccess(read_result=[TextResourceContent(text="hello", uri="viking://doc.md")])
    agent = FakeAgent(capabilities=[cap])

    source = ResourceSource(
        text=TextSpan(value="@viking:doc.md", start=0, end=14),
        client_name="viking",
        uri="viking://doc.md",
    )
    part = FilePartInput(mime="text/plain", url="", source=source)

    result = await extract_user_prompt_from_parts([part], "test-session", agent=agent)
    result_list = list(result)
    assert len(result_list) == 1
    assert result_list[0] == '<resource uri="viking://doc.md">\nhello\n</resource>'


async def test_extract_user_prompt_with_binary_resource() -> None:
    """Resource returns BlobResourceContent → result contains BinaryContent in XML sandwich."""
    from agentpool_server.opencode_server.converters import extract_user_prompt_from_parts
    from agentpool_server.opencode_server.models import FilePartInput
    from agentpool_server.opencode_server.models.common import TextSpan
    from agentpool_server.opencode_server.models.parts import ResourceSource

    blob_data = base64.b64encode(b"img").decode()
    cap = FakeResourceAccess(
        read_result=[
            BlobResourceContent(blob=blob_data, mime_type="image/png", uri="viking://img.png")
        ]
    )
    agent = FakeAgent(capabilities=[cap])

    source = ResourceSource(
        text=TextSpan(value="@viking:img.png", start=0, end=15),
        client_name="viking",
        uri="viking://img.png",
    )
    part = FilePartInput(mime="image/png", url="", source=source)

    result = await extract_user_prompt_from_parts([part], "test-session", agent=agent)
    result_list = list(result)
    assert len(result_list) == 3
    assert result_list[0] == '<resource uri="viking://img.png">\n'
    assert isinstance(result_list[1], BinaryContent)
    assert result_list[1].data == b"img"
    assert result_list[1].media_type == "image/png"
    assert result_list[2] == "\n</resource>"


async def test_extract_user_prompt_resource_no_agent() -> None:
    """agent=None → FilePartInput falls through to generic file handler."""
    from agentpool_server.opencode_server.converters import extract_user_prompt_from_parts
    from agentpool_server.opencode_server.models import FilePartInput
    from agentpool_server.opencode_server.models.common import TextSpan
    from agentpool_server.opencode_server.models.parts import ResourceSource

    source = ResourceSource(
        text=TextSpan(value="@viking:doc.md", start=0, end=14),
        client_name="viking",
        uri="viking://doc.md",
    )
    # Provide a data: URL so the generic file handler can produce content
    part = FilePartInput(
        mime="text/plain",
        url="data:text/plain;base64,aGVsbG8=",  # "hello" base64-encoded
        source=source,
    )

    result = await extract_user_prompt_from_parts([part], "test-session", agent=None)
    result_list = list(result)
    # The generic handler should produce some content (not None)
    assert len(result_list) >= 1


async def test_extract_user_prompt_resource_returns_none() -> None:
    """Resolve returns None → no content appended for that part."""
    from agentpool_server.opencode_server.converters import extract_user_prompt_from_parts
    from agentpool_server.opencode_server.models import FilePartInput, TextPartInput
    from agentpool_server.opencode_server.models.common import TextSpan
    from agentpool_server.opencode_server.models.parts import ResourceSource

    cap = FakeResourceAccess(read_result=None)
    agent = FakeAgent(capabilities=[cap])

    source = ResourceSource(
        text=TextSpan(value="@viking:missing", start=0, end=14),
        client_name="viking",
        uri="viking://missing",
    )
    resource_part = FilePartInput(mime="text/plain", url="", source=source)
    text_part = TextPartInput(text="hello world")

    result = await extract_user_prompt_from_parts(
        [resource_part, text_part], "test-session", agent=agent
    )
    result_list = list(result)
    # Only the text part should appear — resource resolution returned None
    assert len(result_list) == 1
    assert result_list[0] == "hello world"


async def test_extract_user_prompt_mixed_parts() -> None:
    """Text + resource + agent parts all processed."""
    from agentpool_server.opencode_server.converters import extract_user_prompt_from_parts
    from agentpool_server.opencode_server.models import (
        AgentPartInput,
        FilePartInput,
        TextPartInput,
    )
    from agentpool_server.opencode_server.models.common import TextSpan
    from agentpool_server.opencode_server.models.parts import ResourceSource

    cap = FakeResourceAccess(
        read_result=[TextResourceContent(text="resource content", uri="viking://doc")]
    )
    agent = FakeAgent(capabilities=[cap])

    text_part = TextPartInput(text="prefix text")
    source = ResourceSource(
        text=TextSpan(value="@viking:doc", start=0, end=10),
        client_name="viking",
        uri="viking://doc",
    )
    resource_part = FilePartInput(mime="text/plain", url="", source=source)
    agent_part = AgentPartInput(name="researcher")

    result = await extract_user_prompt_from_parts(
        [text_part, resource_part, agent_part], "test-session", agent=agent
    )
    result_list = list(result)
    # 1 text + 1 resource (XML-wrapped) + 1 agent instruction
    assert len(result_list) == 3
    assert result_list[0] == "prefix text"
    assert result_list[1] == '<resource uri="viking://doc">\nresource content\n</resource>'
    assert "researcher" in result_list[2]
