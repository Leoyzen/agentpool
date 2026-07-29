"""Unit tests for VikingCapability.

Covers tasks 8.1-8.14 from openspec/changes/viking-capability/tasks.md.
All tests mock ``AsyncHTTPClient`` — no real Viking server required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pydantic import ValidationError
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.test import TestModel
import pytest

from agentpool.capabilities.viking import VikingCapability
from agentpool.capabilities.viking.tools import build_tools
from agentpool.capabilities.viking.utils import (
    add_line_numbers,
    format_ls_entries,
    format_search_results,
    is_viking_uri,
    truncate_text,
)
from agentpool_config.capabilities import VikingCapabilityConfig


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client() -> AsyncMock:
    """Create a mock AsyncHTTPClient with all SDK methods."""
    client = AsyncMock()
    client.initialize = AsyncMock()
    client.close = AsyncMock()
    client.search = AsyncMock(return_value={"results": []})
    client.find = AsyncMock(return_value={"results": []})
    client.grep = AsyncMock(return_value={"matches": []})
    client.glob = AsyncMock(return_value={"matches": []})
    client.ls = AsyncMock(return_value=[])
    client.read = AsyncMock(return_value="file content")
    client.write = AsyncMock(return_value={"status": "ok"})
    client.mkdir = AsyncMock(return_value=None)
    client.rm = AsyncMock(return_value=None)
    client.link = AsyncMock(return_value=None)
    client.set_tags = AsyncMock(return_value={"status": "ok"})
    client.add_resource = AsyncMock(return_value={"status": "ok"})
    client.create_session = AsyncMock(return_value={"session_id": "test-session"})
    client.add_message = AsyncMock(return_value={"status": "ok"})
    client.commit_session = AsyncMock(return_value={"status": "ok"})
    return client


@pytest.fixture
def viking_cap(mock_client: AsyncMock) -> VikingCapability:
    """Create a VikingCapability with a mock client pre-injected."""
    cap = VikingCapability(mode="all")
    cap._client = mock_client
    return cap


def _make_ctx(session_id: str | None = "test-session") -> MagicMock:
    """Create a mock RunContext with session_id on deps."""
    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.session_id = session_id
    return ctx


def _get_tool(tools: list[Any], name: str) -> Any:
    """Find a tool by name from the list returned by build_tools."""
    return next(t for t in tools if t.__name__ == name)


def _make_request_context(messages: list[Any]) -> ModelRequestContext:
    """Build a minimal ModelRequestContext for before_model_request tests."""
    return ModelRequestContext(
        model=TestModel(),
        messages=messages,
        model_settings=None,
        model_request_parameters=ModelRequestParameters(
            function_tools=[],
            native_tools=[],
        ),
    )


# ---------------------------------------------------------------------------
# 8.1 — Test VikingCapabilityConfig parsing
# ---------------------------------------------------------------------------


class TestVikingCapabilityConfig:
    """Tests for VikingCapabilityConfig parsing and validation."""

    def test_default_config(self) -> None:
        """Default config has mode='all' and all optional fields as None."""
        cfg = VikingCapabilityConfig()
        assert cfg.type == "viking"
        assert cfg.mode == "all"
        assert cfg.url is None
        assert cfg.api_key is None
        assert cfg.account is None
        assert cfg.user is None
        assert cfg.timeout is None
        assert cfg.skills_uri is None
        assert cfg.resources_uri is None
        assert cfg.multimodal_bridge is False
        assert cfg.uploads_uri is None
        assert cfg.public_download_base_url is None

    def test_mode_retrieve(self) -> None:
        """Mode 'retrieve' is accepted."""
        cfg = VikingCapabilityConfig(mode="retrieve")
        assert cfg.mode == "retrieve"

    def test_mode_write(self) -> None:
        """Mode 'write' is accepted."""
        cfg = VikingCapabilityConfig(mode="write")
        assert cfg.mode == "write"

    def test_mode_graph(self) -> None:
        """Mode 'graph' is accepted."""
        cfg = VikingCapabilityConfig(mode="graph")
        assert cfg.mode == "graph"

    def test_mode_all(self) -> None:
        """Mode 'all' is accepted."""
        cfg = VikingCapabilityConfig(mode="all")
        assert cfg.mode == "all"

    def test_mode_invalid_rejected(self) -> None:
        """Invalid mode value is rejected by validation."""
        with pytest.raises(ValidationError):
            VikingCapabilityConfig(mode="invalid")  # type: ignore[arg-type]

    def test_all_fields_populated(self) -> None:
        """All fields can be populated at once."""
        cfg = VikingCapabilityConfig(
            mode="retrieve",
            url="https://viking.example.com",
            api_key="secret-key",
            account="acct123",
            user="alice",
            timeout=30.0,
            skills_uri="viking://user/alice/skills/",
            resources_uri="viking://resources/",
            multimodal_bridge=True,
            uploads_uri="viking://uploads/",
            public_download_base_url="https://download.example.com",
        )
        assert cfg.url == "https://viking.example.com"
        assert cfg.api_key == "secret-key"
        assert cfg.account == "acct123"
        assert cfg.user == "alice"
        assert cfg.timeout == 30.0
        assert cfg.skills_uri == "viking://user/alice/skills/"
        assert cfg.resources_uri == "viking://resources/"
        assert cfg.multimodal_bridge is True
        assert cfg.uploads_uri == "viking://uploads/"
        assert cfg.public_download_base_url == "https://download.example.com"

    def test_discriminator_works(self) -> None:
        """The 'type' field discriminator correctly identifies VikingCapabilityConfig."""
        import typing

        from agentpool_config.capabilities import BuiltinCapabilityConfig

        cfg = VikingCapabilityConfig()
        assert cfg.type == "viking"
        # BuiltinCapabilityConfig is Annotated[Union[...], Field(discriminator="type")]
        # The union type is the first arg; extract its member types.
        union_type = typing.get_args(BuiltinCapabilityConfig)[0]
        member_types = typing.get_args(union_type)
        assert VikingCapabilityConfig in member_types


# ---------------------------------------------------------------------------
# 8.2 — Test __aenter__/__aexit__ lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Tests for __aenter__/__aexit__ lifecycle management."""

    @pytest.mark.asyncio
    async def test_aenter_noop_when_client_already_set(self, mock_client: AsyncMock) -> None:
        """__aenter__ is a no-op when client is already set (for_run copy)."""
        cap = VikingCapability(mode="all")
        cap._client = mock_client

        result = await cap.__aenter__()

        assert result is cap
        assert cap._client is mock_client
        mock_client.initialize.assert_not_called()

    @pytest.mark.asyncio
    async def test_aenter_import_error_when_sdk_not_installed(self) -> None:
        """__aenter__ raises ImportError when openviking_sdk is not installed."""
        cap = VikingCapability(mode="all")
        assert cap._client is None
        with pytest.raises(ImportError):
            await cap.__aenter__()

    @pytest.mark.asyncio
    async def test_aexit_closes_client_when_owned(self, mock_client: AsyncMock) -> None:
        """__aexit__ closes the client when _owns_client is True."""
        cap = VikingCapability(mode="all")
        cap._client = mock_client
        cap._owns_client = True

        await cap.__aexit__(None, None, None)

        mock_client.close.assert_called_once()
        assert cap._client is None

    @pytest.mark.asyncio
    async def test_aexit_does_not_close_client_when_not_owned(self, mock_client: AsyncMock) -> None:
        """__aexit__ does not close the client when _owns_client is False."""
        cap = VikingCapability(mode="all")
        cap._client = mock_client
        cap._owns_client = False

        await cap.__aexit__(None, None, None)

        mock_client.close.assert_not_called()
        assert cap._client is None

    @pytest.mark.asyncio
    async def test_aexit_sets_client_none_regardless_of_ownership(
        self, mock_client: AsyncMock
    ) -> None:
        """__aexit__ sets _client to None even when not owning the client."""
        cap = VikingCapability(mode="all")
        cap._client = mock_client
        cap._owns_client = False

        await cap.__aexit__(None, None, None)

        assert cap._client is None

    @pytest.mark.asyncio
    async def test_aexit_with_exception_still_closes(self, mock_client: AsyncMock) -> None:
        """__aexit__ closes the client even when an exception was raised."""
        cap = VikingCapability(mode="all")
        cap._client = mock_client
        cap._owns_client = True

        await cap.__aexit__(ValueError, ValueError("test"), None)

        mock_client.close.assert_called_once()
        assert cap._client is None


# ---------------------------------------------------------------------------
# 8.3 — Test for_run()
# ---------------------------------------------------------------------------


class TestForRun:
    """Tests for for_run() method."""

    @pytest.mark.asyncio
    async def test_for_run_shares_client(self, mock_client: AsyncMock) -> None:
        """for_run() returns a copy that shares the same client reference."""
        cap = VikingCapability(mode="all", user="alice")
        cap._client = mock_client

        ctx = _make_ctx()
        copy_cap = await cap.for_run(ctx)

        assert copy_cap is not cap
        assert copy_cap._client is mock_client
        assert copy_cap._owns_client is False
        assert copy_cap.mode == cap.mode
        assert copy_cap.user == cap.user

    @pytest.mark.asyncio
    async def test_for_run_preserves_all_fields(self, mock_client: AsyncMock) -> None:
        """for_run() preserves all configuration fields."""
        cap = VikingCapability(
            mode="retrieve",
            url="https://viking.example.com",
            api_key="key",
            account="acct",
            user="alice",
            timeout=30.0,
            skills_uri="viking://user/alice/skills/",
            resources_uri="viking://resources/",
            multimodal_bridge=True,
            uploads_uri="viking://uploads/",
            public_download_base_url="https://dl.example.com",
        )
        cap._client = mock_client

        ctx = _make_ctx()
        copy_cap = await cap.for_run(ctx)

        assert copy_cap.url == cap.url
        assert copy_cap.api_key == cap.api_key
        assert copy_cap.account == cap.account
        assert copy_cap.user == cap.user
        assert copy_cap.timeout == cap.timeout
        assert copy_cap.skills_uri == cap.skills_uri
        assert copy_cap.resources_uri == cap.resources_uri
        assert copy_cap.multimodal_bridge == cap.multimodal_bridge
        assert copy_cap.uploads_uri == cap.uploads_uri
        assert copy_cap.public_download_base_url == cap.public_download_base_url

    @pytest.mark.asyncio
    async def test_for_run_copy_does_not_close_parent_client(self, mock_client: AsyncMock) -> None:
        """Closing the for_run copy does not close the parent's client."""
        cap = VikingCapability(mode="all")
        cap._client = mock_client

        ctx = _make_ctx()
        copy_cap = await cap.for_run(ctx)

        await copy_cap.__aexit__(None, None, None)
        mock_client.close.assert_not_called()
        assert cap._client is mock_client


# ---------------------------------------------------------------------------
# 8.4 — Test each retrieve tool with mocked client
# ---------------------------------------------------------------------------


class TestRetrieveTools:
    """Tests for the 7 retrieve tools."""

    @pytest.mark.asyncio
    async def test_viking_search(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_search maps params correctly and injects session_id."""
        mock_client.search = AsyncMock(
            return_value={"results": [{"uri": "viking://doc.md", "score": 0.9}]}
        )
        tools = build_tools(viking_cap)
        search_tool = _get_tool(tools, "viking_search")

        ctx = _make_ctx(session_id="sess-123")
        result = await search_tool(ctx, query="test query", limit=5, min_score=0.5, level="L1")

        mock_client.search.assert_called_once()
        call_kwargs = mock_client.search.call_args.kwargs
        call_args = mock_client.search.call_args.args
        assert call_args[0] == "test query"
        assert call_kwargs["limit"] == 5
        assert call_kwargs["score_threshold"] == 0.5
        assert call_kwargs["filter"] == {"level": "L1"}
        assert call_kwargs["session_id"] == "sess-123"
        assert "viking://doc.md" in result

    @pytest.mark.asyncio
    async def test_viking_search_no_level(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_search passes filter=None when level is not specified."""
        mock_client.search = AsyncMock(return_value={"results": []})
        tools = build_tools(viking_cap)
        search_tool = _get_tool(tools, "viking_search")

        ctx = _make_ctx()
        await search_tool(ctx, query="test")

        assert mock_client.search.call_args.kwargs["filter"] is None

    @pytest.mark.asyncio
    async def test_viking_search_no_session_id(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_search passes session_id=None when deps has no session_id."""
        mock_client.search = AsyncMock(return_value={"results": []})
        tools = build_tools(viking_cap)
        search_tool = _get_tool(tools, "viking_search")

        # Use a context where deps does not have session_id attribute
        ctx = MagicMock()
        ctx.deps = MagicMock(spec=[])  # spec=[] means no attributes
        await search_tool(ctx, query="test")

        assert mock_client.search.call_args.kwargs["session_id"] is None

    @pytest.mark.asyncio
    async def test_viking_find(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        """viking_find maps params correctly but does NOT pass session_id."""
        mock_client.find = AsyncMock(return_value={"results": [{"uri": "viking://doc.md"}]})
        tools = build_tools(viking_cap)
        find_tool = _get_tool(tools, "viking_find")

        ctx = _make_ctx(session_id="sess-123")
        result = await find_tool(ctx, query="find query", limit=3, min_score=0.2, level="L0")

        mock_client.find.assert_called_once()
        call_kwargs = mock_client.find.call_args.kwargs
        call_args = mock_client.find.call_args.args
        assert call_args[0] == "find query"
        assert call_kwargs["limit"] == 3
        assert call_kwargs["score_threshold"] == 0.2
        assert call_kwargs["filter"] == {"level": "L0"}
        assert "session_id" not in call_kwargs
        assert "viking://doc.md" in result

    @pytest.mark.asyncio
    async def test_viking_recall(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_recall makes multiple find() calls with different context_types."""
        mock_client.find = AsyncMock(
            return_value={"hits": [{"uri": "viking://mem.md", "content": "memory"}]}
        )
        tools = build_tools(viking_cap)
        recall_tool = _get_tool(tools, "viking_recall")

        ctx = _make_ctx()
        result = await recall_tool(ctx, query="remember when")

        assert mock_client.find.call_count == 4
        context_types = [c.kwargs["context_type"] for c in mock_client.find.call_args_list]
        assert "events" in context_types
        assert "entities" in context_types
        assert "preferences" in context_types
        assert "experiences" in context_types
        for call in mock_client.find.call_args_list:
            assert call.kwargs["query"] == "remember when"
        assert "=== events ===" in result
        assert "=== entities ===" in result

    @pytest.mark.asyncio
    async def test_viking_recall_custom_quotas(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_recall respects custom quotas."""
        mock_client.find = AsyncMock(return_value={"results": []})
        tools = build_tools(viking_cap)
        recall_tool = _get_tool(tools, "viking_recall")

        ctx = _make_ctx()
        custom_quotas = {"events": 2, "entities": 3}
        result = await recall_tool(ctx, query="test", quotas=custom_quotas)

        assert mock_client.find.call_count == 2
        quotas_used = [c.kwargs["limit"] for c in mock_client.find.call_args_list]
        assert 2 in quotas_used
        assert 3 in quotas_used
        assert "=== events ===" in result
        assert "=== entities ===" in result

    @pytest.mark.asyncio
    async def test_viking_grep(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        """viking_grep passes uri, pattern, case_insensitive correctly."""
        mock_client.grep = AsyncMock(
            return_value={"matches": [{"line": 10, "text": "matched line"}]}
        )
        tools = build_tools(viking_cap)
        grep_tool = _get_tool(tools, "viking_grep")

        ctx = _make_ctx()
        result = await grep_tool(ctx, uri="viking://doc.md", pattern="hello", case_insensitive=True)

        mock_client.grep.assert_called_once()
        call_args = mock_client.grep.call_args.args
        call_kwargs = mock_client.grep.call_args.kwargs
        assert call_args[0] == "viking://doc.md"
        assert call_args[1] == "hello"
        assert call_kwargs["case_insensitive"] is True
        assert "10: matched line" in result

    @pytest.mark.asyncio
    async def test_viking_grep_no_matches(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_grep returns 'No matches found.' when empty."""
        mock_client.grep = AsyncMock(return_value={"matches": []})
        tools = build_tools(viking_cap)
        grep_tool = _get_tool(tools, "viking_grep")

        ctx = _make_ctx()
        result = await grep_tool(ctx, uri="viking://doc.md", pattern="nothing")
        assert result == "No matches found."

    @pytest.mark.asyncio
    async def test_viking_glob(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        """viking_glob passes pattern and uri correctly."""
        mock_client.glob = AsyncMock(
            return_value={"uris": ["viking://doc1.md", "viking://doc2.md"]}
        )
        tools = build_tools(viking_cap)
        glob_tool = _get_tool(tools, "viking_glob")

        ctx = _make_ctx()
        result = await glob_tool(ctx, pattern="**/*.md", uri="viking://user/")

        mock_client.glob.assert_called_once()
        call_args = mock_client.glob.call_args.args
        call_kwargs = mock_client.glob.call_args.kwargs
        assert call_args[0] == "**/*.md"
        assert call_kwargs["uri"] == "viking://user/"
        assert "viking://doc1.md" in result
        assert "viking://doc2.md" in result

    @pytest.mark.asyncio
    async def test_viking_glob_no_results(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_glob returns 'No URIs found.' when empty."""
        mock_client.glob = AsyncMock(return_value={"uris": []})
        tools = build_tools(viking_cap)
        glob_tool = _get_tool(tools, "viking_glob")

        ctx = _make_ctx()
        result = await glob_tool(ctx, pattern="**/*.txt")
        assert result == "No URIs found."

    @pytest.mark.asyncio
    async def test_viking_ls(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        """viking_ls passes uri and recursive, outputs [dir]/[file] markers."""
        mock_client.ls = AsyncMock(
            return_value=[
                {"name": "folder1", "type": "directory"},
                {"name": "file1.md", "type": "file"},
            ]
        )
        tools = build_tools(viking_cap)
        ls_tool = _get_tool(tools, "viking_ls")

        ctx = _make_ctx()
        result = await ls_tool(ctx, uri="viking://user/alice/", recursive=True)

        mock_client.ls.assert_called_once()
        call_args = mock_client.ls.call_args.args
        call_kwargs = mock_client.ls.call_args.kwargs
        assert call_args[0] == "viking://user/alice/"
        assert call_kwargs["recursive"] is True
        assert "[dir] folder1" in result
        assert "[file] file1.md" in result

    @pytest.mark.asyncio
    async def test_viking_ls_empty(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_ls returns '(empty)' for empty listing."""
        mock_client.ls = AsyncMock(return_value=[])
        tools = build_tools(viking_cap)
        ls_tool = _get_tool(tools, "viking_ls")

        ctx = _make_ctx()
        result = await ls_tool(ctx, uri="viking://empty/")
        assert result == "(empty)"

    @pytest.mark.asyncio
    async def test_viking_read_single_uri(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_read reads a single URI with line number prefixes."""
        mock_client.read = AsyncMock(return_value="line1\nline2\nline3")
        tools = build_tools(viking_cap)
        read_tool = _get_tool(tools, "viking_read")

        ctx = _make_ctx()
        result = await read_tool(ctx, uris="viking://doc.md", line=1, limit=-1)

        mock_client.read.assert_called_once()
        call_args = mock_client.read.call_args.args
        call_kwargs = mock_client.read.call_args.kwargs
        assert call_args[0] == "viking://doc.md"
        assert call_kwargs["offset"] == 0
        assert call_kwargs["limit"] == -1
        assert "1\u2502 line1" in result

    @pytest.mark.asyncio
    async def test_viking_read_line_to_offset_conversion(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_read converts line=51 to offset=50."""
        mock_client.read = AsyncMock(return_value="content")
        tools = build_tools(viking_cap)
        read_tool = _get_tool(tools, "viking_read")

        ctx = _make_ctx()
        await read_tool(ctx, uris="viking://doc.md", line=51)

        assert mock_client.read.call_args.kwargs["offset"] == 50

    @pytest.mark.asyncio
    async def test_viking_read_multi_uri(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_read handles multiple URIs with === {uri} === headers."""
        mock_client.read = AsyncMock(return_value="content")
        tools = build_tools(viking_cap)
        read_tool = _get_tool(tools, "viking_read")

        ctx = _make_ctx()
        result = await read_tool(ctx, uris=["viking://a.md", "viking://b.md"])

        assert mock_client.read.call_count == 2
        assert "=== viking://a.md ===" in result
        assert "=== viking://b.md ===" in result

    @pytest.mark.asyncio
    async def test_viking_read_multi_uri_no_header_for_single(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_read does not add === header for single URI."""
        mock_client.read = AsyncMock(return_value="content")
        tools = build_tools(viking_cap)
        read_tool = _get_tool(tools, "viking_read")

        ctx = _make_ctx()
        result = await read_tool(ctx, uris="viking://single.md")

        assert "===" not in result


# ---------------------------------------------------------------------------
# 8.5 — Test each write tool with mocked client
# ---------------------------------------------------------------------------


class TestWriteTools:
    """Tests for the 6 write tools."""

    @pytest.mark.asyncio
    async def test_viking_remember(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_remember calls create_session -> add_message per msg -> commit_session."""
        tools = build_tools(viking_cap)
        remember_tool = _get_tool(tools, "viking_remember")

        ctx = _make_ctx()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        result = await remember_tool(ctx, messages=messages)

        mock_client.create_session.assert_called_once()
        assert mock_client.add_message.call_count == 2
        mock_client.commit_session.assert_called_once()

        create_sid = mock_client.create_session.call_args.kwargs["session_id"]
        assert mock_client.add_message.call_args_list[0].args[0] == create_sid
        assert mock_client.add_message.call_args_list[0].args[1] == "user"
        assert mock_client.add_message.call_args_list[0].args[2] == "Hello"
        assert mock_client.add_message.call_args_list[1].args[0] == create_sid
        assert mock_client.add_message.call_args_list[1].args[1] == "assistant"
        assert mock_client.add_message.call_args_list[1].args[2] == "Hi there"
        assert mock_client.commit_session.call_args.args[0] == create_sid

        assert "Remembered 2 messages" in result
        assert create_sid in result

    @pytest.mark.asyncio
    async def test_viking_write_default_mode(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_write uses mode='create' by default."""
        tools = build_tools(viking_cap)
        write_tool = _get_tool(tools, "viking_write")

        ctx = _make_ctx()
        result = await write_tool(ctx, uri="viking://new.md", content="hello world")

        mock_client.write.assert_called_once()
        call_args = mock_client.write.call_args.args
        call_kwargs = mock_client.write.call_args.kwargs
        assert call_args[0] == "viking://new.md"
        assert call_args[1] == "hello world"
        assert call_kwargs["mode"] == "create"
        assert "Wrote" in result

    @pytest.mark.asyncio
    async def test_viking_write_replace_mode(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_write passes mode='replace' when specified."""
        tools = build_tools(viking_cap)
        write_tool = _get_tool(tools, "viking_write")

        ctx = _make_ctx()
        await write_tool(ctx, uri="viking://doc.md", content="new", mode="replace")

        assert mock_client.write.call_args.kwargs["mode"] == "replace"

    @pytest.mark.asyncio
    async def test_viking_edit_success(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_edit successfully replaces a string in a document."""
        mock_client.read = AsyncMock(return_value="hello world")
        tools = build_tools(viking_cap)
        edit_tool = _get_tool(tools, "viking_edit")

        ctx = _make_ctx()
        result = await edit_tool(ctx, uri="viking://doc.md", old_string="hello", new_string="hi")

        mock_client.read.assert_called_once()
        mock_client.write.assert_called_once()
        written_content = mock_client.write.call_args.args[1]
        assert written_content == "hi world"
        assert "Replaced 1 occurrence" in result

    @pytest.mark.asyncio
    async def test_viking_edit_multiple_matches_error(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_edit returns error when multiple matches found without replace_all."""
        mock_client.read = AsyncMock(return_value="hello world hello")
        tools = build_tools(viking_cap)
        edit_tool = _get_tool(tools, "viking_edit")

        ctx = _make_ctx()
        result = await edit_tool(
            ctx, uri="viking://doc.md", old_string="hello", new_string="hi", replace_all=False
        )

        mock_client.write.assert_not_called()
        assert "error" in result.lower()
        assert "2 times" in result

    @pytest.mark.asyncio
    async def test_viking_edit_replace_all(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_edit replaces all occurrences when replace_all=True."""
        mock_client.read = AsyncMock(return_value="hello world hello")
        tools = build_tools(viking_cap)
        edit_tool = _get_tool(tools, "viking_edit")

        ctx = _make_ctx()
        result = await edit_tool(
            ctx, uri="viking://doc.md", old_string="hello", new_string="hi", replace_all=True
        )

        mock_client.write.assert_called_once()
        written_content = mock_client.write.call_args.args[1]
        assert written_content == "hi world hi"
        assert "Replaced 2 occurrence" in result

    @pytest.mark.asyncio
    async def test_viking_edit_no_matches_error(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_edit returns error when old_string not found."""
        mock_client.read = AsyncMock(return_value="hello world")
        tools = build_tools(viking_cap)
        edit_tool = _get_tool(tools, "viking_edit")

        ctx = _make_ctx()
        result = await edit_tool(
            ctx, uri="viking://doc.md", old_string="nonexistent", new_string="x"
        )

        mock_client.write.assert_not_called()
        assert "error" in result.lower()
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_viking_mkdir(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        """viking_mkdir passes uri and description correctly."""
        tools = build_tools(viking_cap)
        mkdir_tool = _get_tool(tools, "viking_mkdir")

        ctx = _make_ctx()
        result = await mkdir_tool(ctx, uri="viking://new/dir/", description="My directory")

        mock_client.mkdir.assert_called_once()
        call_args = mock_client.mkdir.call_args.args
        call_kwargs = mock_client.mkdir.call_args.kwargs
        assert call_args[0] == "viking://new/dir/"
        assert call_kwargs["description"] == "My directory"
        assert "Created directory" in result

    @pytest.mark.asyncio
    async def test_viking_mkdir_no_description(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_mkdir works without a description."""
        tools = build_tools(viking_cap)
        mkdir_tool = _get_tool(tools, "viking_mkdir")

        ctx = _make_ctx()
        result = await mkdir_tool(ctx, uri="viking://new/dir/")

        assert mock_client.mkdir.call_args.kwargs["description"] is None
        assert "Created directory" in result

    @pytest.mark.asyncio
    async def test_viking_add_resource(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_add_resource passes all params correctly."""
        mock_client.add_resource = AsyncMock(return_value={"status": "ok", "id": "res-123"})
        tools = build_tools(viking_cap)
        add_tool = _get_tool(tools, "viking_add_resource")

        ctx = _make_ctx()
        result = await add_tool(
            ctx,
            path="/local/file.txt",
            to="viking://user/alice/files/",
            parent="viking://user/alice/",
            processing_mode="auto",
            watch_interval=5.0,
        )

        mock_client.add_resource.assert_called_once()
        call_args = mock_client.add_resource.call_args.args
        call_kwargs = mock_client.add_resource.call_args.kwargs
        assert call_args[0] == "/local/file.txt"
        assert call_kwargs["to"] == "viking://user/alice/files/"
        assert call_kwargs["parent"] == "viking://user/alice/"
        assert call_kwargs["processing_mode"] == "auto"
        assert call_kwargs["watch_interval"] == 5.0
        assert "Added resource" in result

    @pytest.mark.asyncio
    async def test_viking_forget(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_forget calls rm() with the recursive flag."""
        tools = build_tools(viking_cap)
        forget_tool = _get_tool(tools, "viking_forget")

        ctx = _make_ctx()
        result = await forget_tool(ctx, uri="viking://doc.md", recursive=True)

        mock_client.rm.assert_called_once()
        call_args = mock_client.rm.call_args.args
        call_kwargs = mock_client.rm.call_args.kwargs
        assert call_args[0] == "viking://doc.md"
        assert call_kwargs["recursive"] is True
        assert "Removed" in result

    @pytest.mark.asyncio
    async def test_viking_forget_non_recursive(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_forget passes recursive=False by default."""
        tools = build_tools(viking_cap)
        forget_tool = _get_tool(tools, "viking_forget")

        ctx = _make_ctx()
        await forget_tool(ctx, uri="viking://doc.md")

        assert mock_client.rm.call_args.kwargs["recursive"] is False


# ---------------------------------------------------------------------------
# 8.6 — Test each graph tool with mocked client
# ---------------------------------------------------------------------------


class TestGraphTools:
    """Tests for the 2 graph tools."""

    @pytest.mark.asyncio
    async def test_viking_link_single_target(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_link passes from_uri, to_uris (string), reason correctly."""
        tools = build_tools(viking_cap)
        link_tool = _get_tool(tools, "viking_link")

        ctx = _make_ctx()
        result = await link_tool(
            ctx, from_uri="viking://a.md", to_uris="viking://b.md", reason="depends-on"
        )

        mock_client.link.assert_called_once()
        call_args = mock_client.link.call_args.args
        call_kwargs = mock_client.link.call_args.kwargs
        assert call_args[0] == "viking://a.md"
        assert call_args[1] == "viking://b.md"
        assert call_kwargs["reason"] == "depends-on"
        assert "Linked" in result
        assert "viking://a.md" in result
        assert "viking://b.md" in result

    @pytest.mark.asyncio
    async def test_viking_link_multiple_targets(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_link handles a list of target URIs."""
        tools = build_tools(viking_cap)
        link_tool = _get_tool(tools, "viking_link")

        ctx = _make_ctx()
        result = await link_tool(
            ctx,
            from_uri="viking://a.md",
            to_uris=["viking://b.md", "viking://c.md"],
            reason="references",
        )

        mock_client.link.assert_called_once()
        call_args = mock_client.link.call_args.args
        assert call_args[0] == "viking://a.md"
        assert call_args[1] == ["viking://b.md", "viking://c.md"]
        assert "viking://b.md" in result
        assert "viking://c.md" in result

    @pytest.mark.asyncio
    async def test_viking_set_tags(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_set_tags passes uri, tags, recursive correctly."""
        tools = build_tools(viking_cap)
        tags_tool = _get_tool(tools, "viking_set_tags")

        ctx = _make_ctx()
        result = await tags_tool(
            ctx, uri="viking://doc.md", tags=["status=active", "priority=high"], recursive=True
        )

        mock_client.set_tags.assert_called_once()
        call_args = mock_client.set_tags.call_args.args
        call_kwargs = mock_client.set_tags.call_args.kwargs
        assert call_args[0] == "viking://doc.md"
        assert call_args[1] == ["status=active", "priority=high"]
        assert call_kwargs["recursive"] is True
        assert "Set 2 tag" in result

    @pytest.mark.asyncio
    async def test_viking_set_tags_non_recursive(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_set_tags passes recursive=False by default."""
        tools = build_tools(viking_cap)
        tags_tool = _get_tool(tools, "viking_set_tags")

        ctx = _make_ctx()
        await tags_tool(ctx, uri="viking://doc.md", tags=["key=val"])

        assert mock_client.set_tags.call_args.kwargs["recursive"] is False


# ---------------------------------------------------------------------------
# 8.7 — Test viking_read pagination (detailed)
# ---------------------------------------------------------------------------


class TestVikingReadPagination:
    """Detailed tests for viking_read pagination and formatting."""

    @pytest.mark.asyncio
    async def test_line_to_offset_conversion(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """line=1 -> offset=0, line=10 -> offset=9, line=51 -> offset=50."""
        mock_client.read = AsyncMock(return_value="content")
        tools = build_tools(viking_cap)
        read_tool = _get_tool(tools, "viking_read")

        ctx = _make_ctx()
        for line, expected_offset in [(1, 0), (10, 9), (51, 50), (100, 99)]:
            mock_client.read.reset_mock()
            await read_tool(ctx, uris="viking://doc.md", line=line)
            assert mock_client.read.call_args.kwargs["offset"] == expected_offset

    @pytest.mark.asyncio
    async def test_limit_passed_correctly(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """Limit is passed to SDK read() correctly."""
        mock_client.read = AsyncMock(return_value="content")
        tools = build_tools(viking_cap)
        read_tool = _get_tool(tools, "viking_read")

        ctx = _make_ctx()
        await read_tool(ctx, uris="viking://doc.md", line=1, limit=50)
        assert mock_client.read.call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_line_number_prefixes(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_read adds line number prefixes to output."""
        mock_client.read = AsyncMock(return_value="first\nsecond\nthird")
        tools = build_tools(viking_cap)
        read_tool = _get_tool(tools, "viking_read")

        ctx = _make_ctx()
        result = await read_tool(ctx, uris="viking://doc.md", line=1)

        lines = result.split("\n")
        assert len(lines) == 3
        assert "1" in lines[0]
        assert "first" in lines[0]
        assert "2" in lines[1]
        assert "second" in lines[1]
        assert "3" in lines[2]
        assert "third" in lines[2]

    @pytest.mark.asyncio
    async def test_multi_uri_batch_headers(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_read includes === {uri} === headers for multi-URI reads."""
        mock_client.read = AsyncMock(return_value="content")
        tools = build_tools(viking_cap)
        read_tool = _get_tool(tools, "viking_read")

        ctx = _make_ctx()
        uris = ["viking://a.md", "viking://b.md", "viking://c.md"]
        result = await read_tool(ctx, uris=uris)

        assert mock_client.read.call_count == 3
        for uri in uris:
            assert f"=== {uri} ===" in result


# ---------------------------------------------------------------------------
# 8.8 — Test viking_edit (additional edge cases)
# ---------------------------------------------------------------------------


class TestVikingEditEdgeCases:
    """Additional edge case tests for viking_edit."""

    @pytest.mark.asyncio
    async def test_edit_read_modify_write_cycle(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_edit performs a full read-modify-write cycle."""
        original = "The quick brown fox"
        mock_client.read = AsyncMock(return_value=original)
        tools = build_tools(viking_cap)
        edit_tool = _get_tool(tools, "viking_edit")

        ctx = _make_ctx()
        await edit_tool(ctx, uri="viking://doc.md", old_string="quick", new_string="slow")

        mock_client.read.assert_called_once()
        assert mock_client.read.call_args.args[0] == "viking://doc.md"
        mock_client.write.assert_called_once()
        assert mock_client.write.call_args.args[0] == "viking://doc.md"
        assert mock_client.write.call_args.args[1] == "The slow brown fox"
        assert mock_client.write.call_args.kwargs["mode"] == "replace"

    @pytest.mark.asyncio
    async def test_edit_file_not_found(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_edit returns error string when read raises an exception."""
        mock_client.read = AsyncMock(side_effect=FileNotFoundError("not found"))
        tools = build_tools(viking_cap)
        edit_tool = _get_tool(tools, "viking_edit")

        ctx = _make_ctx()
        result = await edit_tool(ctx, uri="viking://missing.md", old_string="old", new_string="new")

        mock_client.write.assert_not_called()
        assert "viking_edit error" in result


# ---------------------------------------------------------------------------
# 8.9 — Test viking_recall (detailed)
# ---------------------------------------------------------------------------


class TestVikingRecallDetailed:
    """Detailed tests for viking_recall quota enforcement and result merging."""

    @pytest.mark.asyncio
    async def test_default_quotas(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """Default quotas are {events: 3, entities: 5, preferences: 3, experiences: 3}."""
        mock_client.find = AsyncMock(return_value={"results": []})
        tools = build_tools(viking_cap)
        recall_tool = _get_tool(tools, "viking_recall")

        ctx = _make_ctx()
        await recall_tool(ctx, query="test")

        calls = mock_client.find.call_args_list
        quota_map = {c.kwargs["context_type"]: c.kwargs["limit"] for c in calls}
        assert quota_map == {"events": 3, "entities": 5, "preferences": 3, "experiences": 3}

    @pytest.mark.asyncio
    async def test_result_merge(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        """Results from multiple find() calls are merged with section headers."""
        mock_client.find = AsyncMock(
            return_value={"hits": [{"uri": "viking://mem.md", "content": "data"}]}
        )
        tools = build_tools(viking_cap)
        recall_tool = _get_tool(tools, "viking_recall")

        ctx = _make_ctx()
        result = await recall_tool(ctx, query="test", max_chars=10000)

        assert "=== events ===" in result
        assert "=== entities ===" in result
        assert "=== preferences ===" in result
        assert "=== experiences ===" in result
        assert result.count("viking://mem.md") == 4

    @pytest.mark.asyncio
    async def test_truncation(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        """Output is truncated when it exceeds max_chars."""
        long_content = "x" * 5000
        mock_client.find = AsyncMock(
            return_value={"hits": [{"uri": "viking://mem.md", "content": long_content}]}
        )
        tools = build_tools(viking_cap)
        recall_tool = _get_tool(tools, "viking_recall")

        ctx = _make_ctx()
        result = await recall_tool(ctx, query="test", max_chars=100)

        assert len(result) <= 200
        assert "truncated" in result


# ---------------------------------------------------------------------------
# 8.10 — Test viking_remember (detailed)
# ---------------------------------------------------------------------------


class TestVikingRememberDetailed:
    """Detailed tests for viking_remember session sequence."""

    @pytest.mark.asyncio
    async def test_session_creation_sequence(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """create_session -> add_message (per msg) -> commit_session in order."""
        tools = build_tools(viking_cap)
        remember_tool = _get_tool(tools, "viking_remember")

        ctx = _make_ctx()
        messages = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "A programming language."},
            {"role": "user", "content": "Thanks!"},
        ]
        result = await remember_tool(ctx, messages=messages)

        mock_client.create_session.assert_called_once()
        assert mock_client.add_message.call_count == 3
        mock_client.commit_session.assert_called_once()

        session_id = mock_client.create_session.call_args.kwargs["session_id"]
        for call in mock_client.add_message.call_args_list:
            assert call.args[0] == session_id
        assert mock_client.commit_session.call_args.args[0] == session_id

        assert "Remembered 3 messages" in result

    @pytest.mark.asyncio
    async def test_remember_single_message(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_remember works with a single message."""
        tools = build_tools(viking_cap)
        remember_tool = _get_tool(tools, "viking_remember")

        ctx = _make_ctx()
        result = await remember_tool(ctx, messages=[{"role": "user", "content": "Hi"}])

        assert mock_client.add_message.call_count == 1
        assert "Remembered 1 messages" in result

    @pytest.mark.asyncio
    async def test_remember_empty_messages(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """viking_remember works with empty messages list."""
        tools = build_tools(viking_cap)
        remember_tool = _get_tool(tools, "viking_remember")

        ctx = _make_ctx()
        result = await remember_tool(ctx, messages=[])

        mock_client.create_session.assert_called_once()
        mock_client.add_message.assert_not_called()
        mock_client.commit_session.assert_called_once()
        assert "Remembered 0 messages" in result


# ---------------------------------------------------------------------------
# 8.11 — Test error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests that all tools return error strings, never raise exceptions."""

    @pytest.mark.asyncio
    async def test_search_error(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        mock_client.search = AsyncMock(side_effect=RuntimeError("connection failed"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_search")(ctx, query="test")
        assert "viking_search error: connection failed" in result

    @pytest.mark.asyncio
    async def test_find_error(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        mock_client.find = AsyncMock(side_effect=RuntimeError("timeout"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_find")(ctx, query="test")
        assert "viking_find error: timeout" in result

    @pytest.mark.asyncio
    async def test_recall_error(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        mock_client.find = AsyncMock(side_effect=RuntimeError("server error"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_recall")(ctx, query="test")
        assert "viking_recall error: server error" in result

    @pytest.mark.asyncio
    async def test_grep_error(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        mock_client.grep = AsyncMock(side_effect=RuntimeError("bad pattern"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_grep")(ctx, uri="viking://doc.md", pattern="test")
        assert "viking_grep error: bad pattern" in result

    @pytest.mark.asyncio
    async def test_glob_error(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        mock_client.glob = AsyncMock(side_effect=RuntimeError("error"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_glob")(ctx, pattern="**/*.md")
        assert "viking_glob error: error" in result

    @pytest.mark.asyncio
    async def test_ls_error(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        mock_client.ls = AsyncMock(side_effect=RuntimeError("not found"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_ls")(ctx, uri="viking://missing/")
        assert "viking_ls error: not found" in result

    @pytest.mark.asyncio
    async def test_read_error(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        mock_client.read = AsyncMock(side_effect=RuntimeError("permission denied"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_read")(ctx, uris="viking://secret.md")
        assert "viking_read error: permission denied" in result

    @pytest.mark.asyncio
    async def test_remember_error(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.create_session = AsyncMock(side_effect=RuntimeError("quota exceeded"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_remember")(
            ctx, messages=[{"role": "user", "content": "hi"}]
        )
        assert "viking_remember error: quota exceeded" in result

    @pytest.mark.asyncio
    async def test_write_error(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        mock_client.write = AsyncMock(side_effect=RuntimeError("disk full"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_write")(ctx, uri="viking://doc.md", content="data")
        assert "viking_write error: disk full" in result

    @pytest.mark.asyncio
    async def test_edit_error(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        mock_client.read = AsyncMock(side_effect=RuntimeError("network error"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_edit")(
            ctx, uri="viking://doc.md", old_string="a", new_string="b"
        )
        assert "viking_edit error: network error" in result

    @pytest.mark.asyncio
    async def test_mkdir_error(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        mock_client.mkdir = AsyncMock(side_effect=RuntimeError("exists"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_mkdir")(ctx, uri="viking://exists/")
        assert "viking_mkdir error: exists" in result

    @pytest.mark.asyncio
    async def test_add_resource_error(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.add_resource = AsyncMock(side_effect=RuntimeError("invalid path"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_add_resource")(ctx, path="/bad/path")
        assert "viking_add_resource error: invalid path" in result

    @pytest.mark.asyncio
    async def test_forget_error(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        mock_client.rm = AsyncMock(side_effect=RuntimeError("protected"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_forget")(ctx, uri="viking://protected.md")
        assert "viking_forget error: protected" in result

    @pytest.mark.asyncio
    async def test_link_error(self, viking_cap: VikingCapability, mock_client: AsyncMock) -> None:
        mock_client.link = AsyncMock(side_effect=RuntimeError("cycle detected"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_link")(
            ctx, from_uri="viking://a.md", to_uris="viking://b.md"
        )
        assert "viking_link error: cycle detected" in result

    @pytest.mark.asyncio
    async def test_set_tags_error(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.set_tags = AsyncMock(side_effect=RuntimeError("invalid tag"))
        tools = build_tools(viking_cap)
        ctx = _make_ctx()
        result = await _get_tool(tools, "viking_set_tags")(ctx, uri="viking://doc.md", tags=["bad"])
        assert "viking_set_tags error: invalid tag" in result

    @pytest.mark.asyncio
    async def test_get_client_not_initialized(self) -> None:
        """_get_client raises RuntimeError when client is not initialized."""
        cap = VikingCapability(mode="all")
        with pytest.raises(RuntimeError, match="not initialized"):
            cap._get_client()


# ---------------------------------------------------------------------------
# 8.12 — Test SkillResource methods
# ---------------------------------------------------------------------------


class TestSkillResource:
    """Tests for SkillResource protocol methods."""

    @pytest.mark.asyncio
    async def test_list_skills_success(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """list_skills returns SkillEntry list with source='remote'."""
        mock_client.ls = AsyncMock(
            return_value=[
                {"name": "ponytail.md", "type": "file"},
                {"name": "brainstorming.md", "type": "file"},
                {"name": "notes", "type": "directory"},
            ]
        )
        skills = await viking_cap.list_skills()

        assert len(skills) == 2
        assert all(s.source == "remote" for s in skills)
        assert all(s.skill_path is None for s in skills)
        names = [s.name for s in skills]
        assert "ponytail" in names
        assert "brainstorming" in names

    @pytest.mark.asyncio
    async def test_list_skills_empty(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """list_skills returns empty list when no skills found."""
        mock_client.ls = AsyncMock(return_value=[])
        skills = await viking_cap.list_skills()
        assert skills == []

    @pytest.mark.asyncio
    async def test_list_skills_error(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """list_skills returns empty list on error."""
        mock_client.ls = AsyncMock(side_effect=RuntimeError("connection failed"))
        skills = await viking_cap.list_skills()
        assert skills == []

    @pytest.mark.asyncio
    async def test_list_skills_not_initialized(self) -> None:
        """list_skills returns empty list when client is not initialized."""
        cap = VikingCapability(mode="all")
        skills = await cap.list_skills()
        assert skills == []

    @pytest.mark.asyncio
    async def test_list_skills_non_list_response(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """list_skills returns empty list when SDK returns non-list."""
        mock_client.ls = AsyncMock(return_value={"error": "unexpected"})
        skills = await viking_cap.list_skills()
        assert skills == []

    @pytest.mark.asyncio
    async def test_list_skills_string_entries(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """list_skills handles string entries from ls."""
        mock_client.ls = AsyncMock(return_value=["skill1.md", "skill2.md", "not_a_skill"])
        skills = await viking_cap.list_skills()

        assert len(skills) == 2
        assert all(s.source == "remote" for s in skills)
        names = [s.name for s in skills]
        assert "skill1" in names
        assert "skill2" in names

    @pytest.mark.asyncio
    async def test_read_skill_success(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """read_skill returns skill content as string."""
        mock_client.read = AsyncMock(return_value="# Ponytail Skill\n\nInstructions...")
        content = await viking_cap.read_skill("ponytail")

        assert content is not None
        assert "Ponytail Skill" in content
        expected_uri = "viking://user/default/skills/ponytail.md"
        assert mock_client.read.call_args.args[0] == expected_uri

    @pytest.mark.asyncio
    async def test_read_skill_not_found(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """read_skill returns None when skill doesn't exist."""
        mock_client.read = AsyncMock(side_effect=FileNotFoundError("not found"))
        content = await viking_cap.read_skill("nonexistent")
        assert content is None

    @pytest.mark.asyncio
    async def test_read_skill_error(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """read_skill returns None on error."""
        mock_client.read = AsyncMock(side_effect=RuntimeError("server error"))
        content = await viking_cap.read_skill("test")
        assert content is None

    @pytest.mark.asyncio
    async def test_read_skill_empty_content(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """read_skill returns None when content is empty."""
        mock_client.read = AsyncMock(return_value="")
        content = await viking_cap.read_skill("empty")
        assert content is None

    @pytest.mark.asyncio
    async def test_read_skill_not_initialized(self) -> None:
        """read_skill returns None when client is not initialized."""
        cap = VikingCapability(mode="all")
        content = await cap.read_skill("test")
        assert content is None

    @pytest.mark.asyncio
    async def test_skill_exists_true(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """skill_exists returns True when skill is found."""
        mock_client.ls = AsyncMock(
            return_value=[
                {"name": "ponytail.md", "type": "file"},
                {"name": "other.md", "type": "file"},
            ]
        )
        exists = await viking_cap.skill_exists("ponytail")
        assert exists is True

    @pytest.mark.asyncio
    async def test_skill_exists_false(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """skill_exists returns False when skill is not found."""
        mock_client.ls = AsyncMock(return_value=[{"name": "other.md"}])
        exists = await viking_cap.skill_exists("nonexistent")
        assert exists is False

    @pytest.mark.asyncio
    async def test_skill_exists_error(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """skill_exists returns False on error."""
        mock_client.ls = AsyncMock(side_effect=RuntimeError("error"))
        exists = await viking_cap.skill_exists("test")
        assert exists is False

    @pytest.mark.asyncio
    async def test_skill_exists_not_initialized(self) -> None:
        """skill_exists returns False when client is not initialized."""
        cap = VikingCapability(mode="all")
        exists = await cap.skill_exists("test")
        assert exists is False

    @pytest.mark.asyncio
    async def test_skill_exists_non_list_response(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """skill_exists returns False when SDK returns non-list."""
        mock_client.ls = AsyncMock(return_value="unexpected")
        exists = await viking_cap.skill_exists("test")
        assert exists is False

    def test_resolve_skills_uri_with_override(self) -> None:
        """_resolve_skills_uri returns override when set."""
        cap = VikingCapability(mode="all", skills_uri="viking://custom/skills/")
        assert cap._resolve_skills_uri() == "viking://custom/skills/"

    def test_resolve_skills_uri_default(self) -> None:
        """_resolve_skills_uri uses default convention when no override."""
        cap = VikingCapability(mode="all", user="alice")
        assert cap._resolve_skills_uri() == "viking://user/alice/skills/"

    def test_resolve_skills_uri_default_user(self) -> None:
        """_resolve_skills_uri uses 'default' when user is None."""
        cap = VikingCapability(mode="all")
        assert cap._resolve_skills_uri() == "viking://user/default/skills/"


# ---------------------------------------------------------------------------
# 8.13 — Test mode filtering
# ---------------------------------------------------------------------------


class TestModeFiltering:
    """Tests that mode filtering exposes the correct number of tools."""

    def test_retrieve_mode_7_tools(self) -> None:
        """Retrieve mode exposes 7 tools."""
        cap = VikingCapability(mode="retrieve")
        cap._client = AsyncMock()
        tools = build_tools(cap)
        assert len(tools) == 7
        names = {t.__name__ for t in tools}
        assert names == {
            "viking_search",
            "viking_find",
            "viking_recall",
            "viking_grep",
            "viking_glob",
            "viking_ls",
            "viking_read",
        }

    def test_write_mode_6_tools(self) -> None:
        """Write mode exposes 6 tools."""
        cap = VikingCapability(mode="write")
        cap._client = AsyncMock()
        tools = build_tools(cap)
        assert len(tools) == 6
        names = {t.__name__ for t in tools}
        assert names == {
            "viking_remember",
            "viking_write",
            "viking_edit",
            "viking_mkdir",
            "viking_add_resource",
            "viking_forget",
        }

    def test_graph_mode_2_tools(self) -> None:
        """Graph mode exposes 2 tools."""
        cap = VikingCapability(mode="graph")
        cap._client = AsyncMock()
        tools = build_tools(cap)
        assert len(tools) == 2
        names = {t.__name__ for t in tools}
        assert names == {"viking_link", "viking_set_tags"}

    def test_all_mode_15_tools(self) -> None:
        """All mode exposes 15 tools."""
        cap = VikingCapability(mode="all")
        cap._client = AsyncMock()
        tools = build_tools(cap)
        assert len(tools) == 15

    def test_get_toolset_retrieve(self) -> None:
        """get_toolset() returns a FunctionToolset with 7 tools for retrieve mode."""
        from pydantic_ai.toolsets import FunctionToolset

        cap = VikingCapability(mode="retrieve")
        cap._client = AsyncMock()
        toolset = cap.get_toolset()
        assert toolset is not None
        assert isinstance(toolset, FunctionToolset)
        tool_names = list(toolset.tools.keys())  # type: ignore[attr-defined]
        assert len(tool_names) == 7

    def test_get_toolset_write(self) -> None:
        """get_toolset() returns a FunctionToolset with 6 tools for write mode."""
        from pydantic_ai.toolsets import FunctionToolset

        cap = VikingCapability(mode="write")
        cap._client = AsyncMock()
        toolset = cap.get_toolset()
        assert toolset is not None
        assert isinstance(toolset, FunctionToolset)
        tool_names = list(toolset.tools.keys())  # type: ignore[attr-defined]
        assert len(tool_names) == 6

    def test_get_toolset_graph(self) -> None:
        """get_toolset() returns a FunctionToolset with 2 tools for graph mode."""
        from pydantic_ai.toolsets import FunctionToolset

        cap = VikingCapability(mode="graph")
        cap._client = AsyncMock()
        toolset = cap.get_toolset()
        assert toolset is not None
        assert isinstance(toolset, FunctionToolset)
        tool_names = list(toolset.tools.keys())  # type: ignore[attr-defined]
        assert len(tool_names) == 2

    def test_get_toolset_all(self) -> None:
        """get_toolset() returns a FunctionToolset with 15 tools for all mode."""
        from pydantic_ai.toolsets import FunctionToolset

        cap = VikingCapability(mode="all")
        cap._client = AsyncMock()
        toolset = cap.get_toolset()
        assert toolset is not None
        assert isinstance(toolset, FunctionToolset)
        tool_names = list(toolset.tools.keys())  # type: ignore[attr-defined]
        assert len(tool_names) == 15

    def test_get_toolset_id_is_viking(self) -> None:
        """get_toolset() returns a FunctionToolset with id='viking'."""
        cap = VikingCapability(mode="all")
        cap._client = AsyncMock()
        toolset = cap.get_toolset()
        assert toolset is not None
        assert toolset.id == "viking"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 8.14 — Test get_instructions()
# ---------------------------------------------------------------------------


class TestGetInstructions:
    """Tests for get_instructions() method."""

    def test_returns_non_empty_string(self) -> None:
        """get_instructions() returns a non-empty string."""
        cap = VikingCapability(mode="all")
        instructions = cap.get_instructions()
        assert instructions is not None
        assert isinstance(instructions, str)
        assert len(instructions) > 0

    def test_contains_two_step_retrieval(self) -> None:
        """Instructions contain the two-step retrieval pattern section."""
        cap = VikingCapability(mode="all")
        instructions = cap.get_instructions()
        assert instructions is not None
        assert "Two-Step Retrieval" in instructions
        assert "Search" in instructions
        assert "Read" in instructions

    def test_contains_tool_selection_priority(self) -> None:
        """Instructions contain the tool selection priority section."""
        cap = VikingCapability(mode="all")
        instructions = cap.get_instructions()
        assert instructions is not None
        assert "Tool Selection Priority" in instructions

    def test_contains_three_tier_model(self) -> None:
        """Instructions contain the three-tier content model section."""
        cap = VikingCapability(mode="all")
        instructions = cap.get_instructions()
        assert instructions is not None
        assert "Three-Tier Content Model" in instructions

    def test_contains_writing_strategy(self) -> None:
        """Instructions contain the writing strategy section."""
        cap = VikingCapability(mode="all")
        instructions = cap.get_instructions()
        assert instructions is not None
        assert "Writing Strategy" in instructions

    def test_contains_uri_conventions(self) -> None:
        """Instructions contain URI conventions section."""
        cap = VikingCapability(mode="all")
        instructions = cap.get_instructions()
        assert instructions is not None
        assert "URI Conventions" in instructions

    def test_contains_memory_tools(self) -> None:
        """Instructions contain memory tools section."""
        cap = VikingCapability(mode="all")
        instructions = cap.get_instructions()
        assert instructions is not None
        assert "Memory Tools" in instructions

    def test_instructions_consistent_across_modes(self) -> None:
        """Instructions are the same regardless of mode."""
        cap_all = VikingCapability(mode="all")
        cap_retrieve = VikingCapability(mode="retrieve")
        assert cap_all.get_instructions() == cap_retrieve.get_instructions()

    def test_on_change_returns_none(self) -> None:
        """on_change() returns None."""
        cap = VikingCapability(mode="all")
        assert cap.on_change() is None

    def test_has_wrap_node_run_false(self) -> None:
        """has_wrap_node_run returns False."""
        cap = VikingCapability(mode="all")
        assert cap.has_wrap_node_run is False


# ---------------------------------------------------------------------------
# Utils tests (supplementary)
# ---------------------------------------------------------------------------


class TestUtils:
    """Tests for utility functions in utils.py."""

    def test_format_search_results_dict_with_hits(self) -> None:
        results = {"hits": [{"uri": "viking://doc.md", "score": 0.9, "content": "hello"}]}
        formatted = format_search_results(results)
        assert "viking://doc.md" in formatted
        assert "0.9000" in formatted
        assert "hello" in formatted

    def test_format_search_results_dict_with_results(self) -> None:
        results = {"results": [{"uri": "viking://doc.md"}]}
        formatted = format_search_results(results)
        assert "viking://doc.md" in formatted

    def test_format_search_results_list(self) -> None:
        results = [{"uri": "viking://doc.md", "content": "data"}]
        formatted = format_search_results(results)
        assert "viking://doc.md" in formatted

    def test_format_search_results_empty(self) -> None:
        assert format_search_results([]) == "No results found."
        assert format_search_results({}) == "No results found."

    def test_format_ls_entries_with_markers(self) -> None:
        entries = [
            {"name": "folder1", "type": "directory"},
            {"name": "file1.md", "type": "file"},
        ]
        formatted = format_ls_entries(entries)
        assert "[dir] folder1" in formatted
        assert "[file] file1.md" in formatted

    def test_format_ls_entries_empty(self) -> None:
        assert format_ls_entries([]) == "(empty)"

    def test_format_ls_entries_string(self) -> None:
        formatted = format_ls_entries(["doc.md"])
        assert "[file] doc.md" in formatted

    def test_add_line_numbers(self) -> None:
        result = add_line_numbers("a\nb\nc", start_line=1)
        lines = result.split("\n")
        assert "1" in lines[0]
        assert "a" in lines[0]
        assert "2" in lines[1]
        assert "b" in lines[1]
        assert "3" in lines[2]
        assert "c" in lines[2]

    def test_add_line_numbers_start_line(self) -> None:
        result = add_line_numbers("a\nb", start_line=10)
        lines = result.split("\n")
        assert "10" in lines[0]
        assert "11" in lines[1]

    def test_add_line_numbers_empty(self) -> None:
        assert add_line_numbers("") == ""

    def test_is_viking_uri_true(self) -> None:
        assert is_viking_uri("viking://user/alice/doc.md") is True

    def test_is_viking_uri_false(self) -> None:
        assert is_viking_uri("https://example.com") is False
        assert is_viking_uri("file:///local/path") is False

    def test_truncate_text_no_truncation(self) -> None:
        assert truncate_text("short", 100) == "short"

    def test_truncate_text_with_truncation(self) -> None:
        text = "x" * 200
        result = truncate_text(text, 100)
        assert len(result) < 200
        assert "truncated" in result

    def test_truncate_text_exact_length(self) -> None:
        text = "x" * 50
        assert truncate_text(text, 50) == text


# ---------------------------------------------------------------------------
# Phase 5: ResourceAccess Protocol Tests
# ---------------------------------------------------------------------------


class TestResourceAccessProtocol:
    """Tests for ResourceAccess Protocol implementation (Phase 5)."""

    def test_isinstance_resource_access(self, viking_cap: VikingCapability) -> None:
        """VikingCapability should be recognized as ResourceAccess."""
        from agentpool.capabilities.resource_protocols import ResourceAccess

        assert isinstance(viking_cap, ResourceAccess)

    def test_resolve_resources_uri_default(self) -> None:
        cap = VikingCapability()
        assert cap._resolve_resources_uri() == "viking://resources/"

    def test_resolve_resources_uri_override(self) -> None:
        cap = VikingCapability(resources_uri="viking://resources/plm/templates/")
        assert cap._resolve_resources_uri() == "viking://resources/plm/templates/"

    async def test_list_resources_success(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.ls = AsyncMock(
            return_value=[
                {"name": "doc1.md", "uri": "viking://resources/doc1.md", "isDir": False},
                {"name": "doc2.txt", "uri": "viking://resources/doc2.txt", "isDir": False},
                {"name": "subdir", "uri": "viking://resources/subdir", "isDir": True},
                {
                    "name": "doc3.md",
                    "uri": "viking://resources/doc3.md",
                    "isDir": False,
                    "meta": {"abstract": "A test document"},
                },
            ]
        )
        result = await viking_cap.list_resources()
        assert len(result) == 3  # subdir excluded
        assert result[0].name == "doc1.md"
        assert result[0].uri == "viking://resources/doc1.md"
        assert result[0].mime_type == "text/markdown"
        assert result[1].name == "doc2.txt"
        assert result[1].mime_type == ""
        assert result[2].description == "A test document"

    async def test_list_resources_empty(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.ls = AsyncMock(return_value=[])
        result = await viking_cap.list_resources()
        assert result == []

    async def test_list_resources_error(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.ls = AsyncMock(side_effect=RuntimeError("network error"))
        result = await viking_cap.list_resources()
        assert result == []

    async def test_list_resources_not_list(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.ls = AsyncMock(return_value={"error": "bad"})
        result = await viking_cap.list_resources()
        assert result == []

    async def test_read_resource_success(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.read = AsyncMock(return_value="resource content here")
        result = await viking_cap.read_resource("viking://resources/doc.md")
        assert result is not None
        assert len(result) == 1
        assert result[0].text == "resource content here"
        assert result[0].uri == "viking://resources/doc.md"
        assert result[0].mime_type == "text/markdown"

    async def test_read_resource_non_markdown(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.read = AsyncMock(return_value="plain text")
        result = await viking_cap.read_resource("viking://resources/doc.txt")
        assert result is not None
        assert result[0].mime_type is None

    async def test_read_resource_empty(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.read = AsyncMock(return_value="")
        result = await viking_cap.read_resource("viking://resources/missing.md")
        assert result is None

    async def test_read_resource_error(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.read = AsyncMock(side_effect=RuntimeError("not found"))
        result = await viking_cap.read_resource("viking://resources/missing.md")
        assert result is None

    async def test_resource_exists_true(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.ls = AsyncMock(
            return_value=[
                {"name": "doc.md", "uri": "viking://resources/doc.md"},
                {"name": "other.md", "uri": "viking://resources/other.md"},
            ]
        )
        result = await viking_cap.resource_exists("viking://resources/doc.md")
        assert result is True

    async def test_resource_exists_false(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.ls = AsyncMock(
            return_value=[{"name": "other.md", "uri": "viking://resources/other.md"}]
        )
        result = await viking_cap.resource_exists("viking://resources/doc.md")
        assert result is False

    async def test_resource_exists_error(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.ls = AsyncMock(side_effect=RuntimeError("network error"))
        result = await viking_cap.resource_exists("viking://resources/doc.md")
        assert result is False

    async def test_resource_exists_not_list(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        mock_client.ls = AsyncMock(return_value="not a list")
        result = await viking_cap.resource_exists("viking://resources/doc.md")
        assert result is False


# ---------------------------------------------------------------------------
# Phase 6: Multimodal Bridge Tests
# ---------------------------------------------------------------------------


class TestMultimodalBridge:
    """Tests for multimodal bridge implementation (Phase 6)."""

    def test_supports_modality_no_caps(self) -> None:
        cap = VikingCapability()
        assert cap._supports_modality("image/png") is False
        assert cap._supports_modality("audio/mpeg") is False

    def test_supports_modality_image(self) -> None:
        from agentpool_config.model_capabilities import ModelCapabilities

        cap = VikingCapability(model_capabilities=ModelCapabilities(image_input=True))
        assert cap._supports_modality("image/png") is True
        assert cap._supports_modality("image/jpeg") is True

    def test_supports_modality_image_false(self) -> None:
        from agentpool_config.model_capabilities import ModelCapabilities

        cap = VikingCapability(model_capabilities=ModelCapabilities(image_input=False))
        assert cap._supports_modality("image/png") is False

    def test_supports_modality_audio(self) -> None:
        from agentpool_config.model_capabilities import ModelCapabilities

        cap = VikingCapability(model_capabilities=ModelCapabilities(audio_input=True))
        assert cap._supports_modality("audio/mpeg") is True

    def test_supports_modality_video(self) -> None:
        from agentpool_config.model_capabilities import ModelCapabilities

        cap = VikingCapability(model_capabilities=ModelCapabilities(video_input=True))
        assert cap._supports_modality("video/mp4") is True

    def test_supports_modality_document(self) -> None:
        from agentpool_config.model_capabilities import ModelCapabilities

        cap = VikingCapability(model_capabilities=ModelCapabilities(document_input=True))
        assert cap._supports_modality("application/pdf") is True

    def test_supports_modality_unknown(self) -> None:
        from agentpool_config.model_capabilities import ModelCapabilities

        cap = VikingCapability(model_capabilities=ModelCapabilities(image_input=True))
        assert cap._supports_modality("application/zip") is False

    def test_guess_extension_known(self) -> None:
        from agentpool.capabilities.viking import _guess_extension

        assert _guess_extension("image/png") == "png"
        assert _guess_extension("image/jpeg") == "jpg"
        assert _guess_extension("audio/mpeg") == "mp3"
        assert _guess_extension("video/mp4") == "mp4"
        assert _guess_extension("application/pdf") == "pdf"

    def test_guess_extension_unknown(self) -> None:
        from agentpool.capabilities.viking import _guess_extension

        assert _guess_extension("application/zip") == "bin"

    async def test_before_model_request_disabled(self, viking_cap: VikingCapability) -> None:
        """Should return request_context unchanged when bridge is disabled."""
        rc = _make_request_context([])
        result = await viking_cap.before_model_request(MagicMock(), rc)
        assert result is rc

    async def test_before_model_request_no_client(self) -> None:
        """Should return request_context unchanged when client is None."""
        cap = VikingCapability(multimodal_bridge=True)
        cap._client = None
        rc = _make_request_context([])
        result = await cap.before_model_request(MagicMock(), rc)
        assert result is rc

    async def test_before_model_request_no_binary(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """Should return request_context unchanged when no binary content."""
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        cap = VikingCapability(multimodal_bridge=True)
        cap._client = mock_client

        msg = ModelRequest(parts=[UserPromptPart(content="hello world")])
        rc = _make_request_context([msg])
        result = await cap.before_model_request(MagicMock(), rc)
        assert result is rc  # No modification

    async def test_before_model_request_text_only_model(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """Text-only model: binary should be replaced with text reference."""
        from pydantic_ai.messages import (
            BinaryContent,
            ModelRequest,
            TextPart,
            UserPromptPart,
        )

        from agentpool_config.model_capabilities import ModelCapabilities

        cap = VikingCapability(
            multimodal_bridge=True,
            model_capabilities=ModelCapabilities(image_input=False),
        )
        cap._client = mock_client
        mock_client.write = AsyncMock(return_value={"status": "ok"})

        binary = BinaryContent(data=b"\x89PNG", media_type="image/png")
        msg = ModelRequest(parts=[UserPromptPart(content=["look at this", binary])])
        rc = _make_request_context([msg])
        result = await cap.before_model_request(MagicMock(), rc)

        assert result is not rc
        mock_client.write.assert_called_once()
        new_msg = result.messages[0]
        content = new_msg.parts[0].content
        text_parts = [c for c in content if isinstance(c, TextPart)]
        binary_parts = [c for c in content if isinstance(c, BinaryContent)]
        assert len(text_parts) == 1
        assert "viking://" in text_parts[0].content
        assert len(binary_parts) == 0

    async def test_before_model_request_multimodal_with_url(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """Multimodal model + public_download_base_url: replace with HTTP URL."""
        from pydantic_ai.messages import (
            BinaryContent,
            ModelRequest,
            TextPart,
            UserPromptPart,
        )

        from agentpool_config.model_capabilities import ModelCapabilities

        cap = VikingCapability(
            multimodal_bridge=True,
            public_download_base_url="https://download.example.com",
            model_capabilities=ModelCapabilities(image_input=True),
        )
        cap._client = mock_client
        mock_client.write = AsyncMock(return_value={"status": "ok"})

        binary = BinaryContent(data=b"\x89PNG", media_type="image/png")
        msg = ModelRequest(parts=[UserPromptPart(content=["look", binary])])
        rc = _make_request_context([msg])
        result = await cap.before_model_request(MagicMock(), rc)

        assert result is not rc
        new_msg = result.messages[0]
        content = new_msg.parts[0].content
        text_parts = [c for c in content if isinstance(c, TextPart)]
        assert len(text_parts) == 1
        assert text_parts[0].content.startswith("https://download.example.com?uri=")

    async def test_before_model_request_multimodal_no_url(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """Multimodal model + no URL: keep original binary (persisted)."""
        from pydantic_ai.messages import (
            BinaryContent,
            ModelRequest,
            UserPromptPart,
        )

        from agentpool_config.model_capabilities import ModelCapabilities

        cap = VikingCapability(
            multimodal_bridge=True,
            model_capabilities=ModelCapabilities(image_input=True),
        )
        cap._client = mock_client
        mock_client.write = AsyncMock(return_value={"status": "ok"})

        binary = BinaryContent(data=b"\x89PNG", media_type="image/png")
        msg = ModelRequest(parts=[UserPromptPart(content=["look", binary])])
        rc = _make_request_context([msg])
        result = await cap.before_model_request(MagicMock(), rc)
        assert result is rc  # No modification — binary kept as-is

    async def test_before_model_request_upload_failure(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        """Upload failure: keep original binary content."""
        from pydantic_ai.messages import (
            BinaryContent,
            ModelRequest,
            UserPromptPart,
        )

        from agentpool_config.model_capabilities import ModelCapabilities

        cap = VikingCapability(
            multimodal_bridge=True,
            model_capabilities=ModelCapabilities(image_input=False),
        )
        cap._client = mock_client
        mock_client.write = AsyncMock(side_effect=RuntimeError("upload failed"))

        binary = BinaryContent(data=b"\x89PNG", media_type="image/png")
        msg = ModelRequest(parts=[UserPromptPart(content=["look", binary])])
        rc = _make_request_context([msg])
        result = await cap.before_model_request(MagicMock(), rc)
        assert result is rc  # Upload failed — keep original

    async def test_upload_binary_success(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        from pydantic_ai.messages import BinaryContent

        mock_client.write = AsyncMock(return_value={"status": "ok"})
        binary = BinaryContent(data=b"\x89PNG test", media_type="image/png")
        uri = await viking_cap._upload_binary(binary)
        assert uri is not None
        assert uri.startswith("viking://user/default/memories/uploads/")
        assert uri.endswith(".png")
        mock_client.write.assert_called_once()
        call_kwargs = mock_client.write.call_args
        assert call_kwargs.kwargs["mode"] == "create"

    async def test_upload_binary_custom_uploads_uri(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        from pydantic_ai.messages import BinaryContent

        cap = VikingCapability(uploads_uri="viking://custom/uploads/")
        cap._client = mock_client
        mock_client.write = AsyncMock(return_value={"status": "ok"})
        binary = BinaryContent(data=b"data", media_type="image/jpeg")
        uri = await cap._upload_binary(binary)
        assert uri is not None
        assert uri.startswith("viking://custom/uploads/")
        assert uri.endswith(".jpg")

    async def test_upload_binary_error(
        self, viking_cap: VikingCapability, mock_client: AsyncMock
    ) -> None:
        from pydantic_ai.messages import BinaryContent

        mock_client.write = AsyncMock(side_effect=RuntimeError("write failed"))
        binary = BinaryContent(data=b"data", media_type="image/png")
        uri = await viking_cap._upload_binary(binary)
        assert uri is None

    async def test_upload_binary_no_client(self) -> None:
        from pydantic_ai.messages import BinaryContent

        cap = VikingCapability()
        binary = BinaryContent(data=b"data", media_type="image/png")
        uri = await cap._upload_binary(binary)
        assert uri is None

    async def test_for_run_preserves_model_capabilities(self) -> None:
        from agentpool_config.model_capabilities import ModelCapabilities

        caps = ModelCapabilities(image_input=True)
        cap = VikingCapability(model_capabilities=caps, multimodal_bridge=True)
        copy = await cap.for_run(MagicMock())
        assert copy.model_capabilities is caps
        assert copy.multimodal_bridge is True
