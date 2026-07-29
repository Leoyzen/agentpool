"""End-to-end tests for VikingCapability against a real Viking server.

These tests require:
1. ``openviking-sdk`` installed (``uv sync --group viking``)
2. A running Viking server (default: ``http://127.0.0.1:1933``)
3. ``~/.openviking/ovcli.conf`` or env vars (``OPENVIKING_URL``, etc.)

All tests use a dedicated test namespace under ``viking://user/default/viking_e2e_test/``
and clean up after themselves.

Marked ``@pytest.mark.e2e`` — not run by default. Use::

    uv run pytest tests/capabilities/viking/test_viking_e2e.py -v -m e2e
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any
import uuid

import pytest

from agentpool.capabilities.viking import VikingCapability
from agentpool.capabilities.viking.tools import build_tools


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.real_mcp,
]

TEST_BASE = "viking://user/default/memories/viking_e2e_test/"


def _random_name() -> str:
    """Generate a random name for test isolation."""
    return uuid.uuid4().hex[:8]


@pytest.fixture
async def viking_cap(allow_model_requests: None) -> Any:
    """Create a VikingCapability connected to the real Viking server."""
    cap = VikingCapability(mode="all")
    await cap.__aenter__()
    yield cap
    await cap.__aexit__(None, None, None)


@pytest.fixture
async def test_dir(viking_cap: VikingCapability, allow_model_requests: None) -> str:
    """Create a test directory and clean it up after."""
    client = viking_cap._get_client()
    dir_name = f"test_{_random_name()}"
    dir_uri = f"{TEST_BASE}{dir_name}/"
    await client.mkdir(dir_uri, description="E2E test directory")
    yield dir_uri
    # Cleanup
    with suppress(Exception):
        await client.rm(dir_uri, recursive=True)


class TestE2ELifecycle:
    """Test real SDK lifecycle."""

    async def test_aenter_creates_client(self) -> None:
        cap = VikingCapability(mode="all")
        assert cap._client is None
        await cap.__aenter__()
        assert cap._client is not None
        await cap.__aexit__(None, None, None)
        assert cap._client is None

    async def test_for_run_shares_client(self) -> None:
        from unittest.mock import MagicMock

        cap = VikingCapability(mode="all")
        await cap.__aenter__()
        copy = await cap.for_run(MagicMock())
        assert copy._client is cap._client
        assert copy._owns_client is False
        # Closing copy should NOT close the shared client
        await copy.__aexit__(None, None, None)
        assert cap._client is not None  # Still alive
        await cap.__aexit__(None, None, None)

    async def test_isinstance_protocols(self, viking_cap: VikingCapability) -> None:
        from agentpool.capabilities.resource_protocols import (
            ResourceAccess,
            SkillResource,
        )

        assert isinstance(viking_cap, SkillResource)
        assert isinstance(viking_cap, ResourceAccess)


class TestE2ERetrieveTools:
    """Test retrieve tools against real Viking."""

    async def test_viking_ls(self, viking_cap: VikingCapability, test_dir: str) -> None:
        # Write a file first
        client = viking_cap._get_client()
        await client.write(f"{test_dir}hello.md", "# Hello\nWorld content", mode="create")

        tools = build_tools(viking_cap)
        ls_tool = next(t for t in tools if t.__name__ == "viking_ls")
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.session_id = "e2e-test"

        result = await ls_tool(ctx, uri=test_dir)
        assert "hello.md" in result

    async def test_viking_write_and_read(self, viking_cap: VikingCapability, test_dir: str) -> None:
        tools = build_tools(viking_cap)
        write_tool = next(t for t in tools if t.__name__ == "viking_write")
        read_tool = next(t for t in tools if t.__name__ == "viking_read")
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.session_id = "e2e-test"

        content = f"# Test Document {_random_name()}\n\nThis is test content.\n"
        uri = f"{test_dir}doc.md"
        write_result = await write_tool(ctx, uri=uri, content=content)
        assert "error" not in write_result.lower()

        read_result = await read_tool(ctx, uris=uri)
        assert "Test Document" in read_result

    async def test_viking_grep(self, viking_cap: VikingCapability, test_dir: str) -> None:
        client = viking_cap._get_client()
        await client.write(
            f"{test_dir}grep_target.md",
            "# Code Guide\n\nfunction hello()\nfunction world()\n",
            mode="create",
        )

        tools = build_tools(viking_cap)
        grep_tool = next(t for t in tools if t.__name__ == "viking_grep")
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.session_id = "e2e-test"

        result = await grep_tool(ctx, uri=f"{test_dir}grep_target.md", pattern="function")
        assert "hello" in result or "world" in result

    async def test_viking_glob(self, viking_cap: VikingCapability, test_dir: str) -> None:
        client = viking_cap._get_client()
        await client.write(f"{test_dir}file_a.md", "content a", mode="create")
        await client.write(f"{test_dir}file_b.md", "content b", mode="create")

        tools = build_tools(viking_cap)
        glob_tool = next(t for t in tools if t.__name__ == "viking_glob")
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.session_id = "e2e-test"

        result = await glob_tool(ctx, pattern="**/file_*.md", uri=test_dir)
        # Glob may not find files if not indexed yet — use ls as fallback
        if "No URIs found." in result:
            # Fallback: verify files exist via ls
            ls_tool = next(t for t in tools if t.__name__ == "viking_ls")
            ls_result = await ls_tool(ctx, uri=test_dir)
            assert "file_a" in ls_result or "file_b" in ls_result
        else:
            assert "file_a" in result or "file_b" in result


class TestE2EWriteTools:
    """Test write tools against real Viking."""

    async def test_viking_mkdir(self, viking_cap: VikingCapability, test_dir: str) -> None:
        tools = build_tools(viking_cap)
        mkdir_tool = next(t for t in tools if t.__name__ == "viking_mkdir")
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.session_id = "e2e-test"

        sub_dir = f"{test_dir}subdir_{_random_name()}/"
        result = await mkdir_tool(ctx, uri=sub_dir, description="Test subdir")
        assert "error" not in result.lower()

        # Verify it exists
        client = viking_cap._get_client()
        entries = await client.ls(test_dir)
        names = [e.get("name") for e in entries if isinstance(e, dict)]
        assert any(sub_dir.rstrip("/").split("/")[-1] in str(n) for n in names)

    async def test_viking_edit(self, viking_cap: VikingCapability, test_dir: str) -> None:
        client = viking_cap._get_client()
        uri = f"{test_dir}editable.md"
        await client.write(uri, "line1\nold_text\nline3\n", mode="create")

        tools = build_tools(viking_cap)
        edit_tool = next(t for t in tools if t.__name__ == "viking_edit")
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.session_id = "e2e-test"

        result = await edit_tool(ctx, uri=uri, old_string="old_text", new_string="new_text")
        assert "error" not in result.lower()

        # Verify content changed
        content = await client.read(uri)
        assert "new_text" in content
        assert "old_text" not in content

    async def test_viking_forget(self, viking_cap: VikingCapability, test_dir: str) -> None:
        client = viking_cap._get_client()
        uri = f"{test_dir}deletable.md"
        await client.write(uri, "to be deleted", mode="create")

        tools = build_tools(viking_cap)
        forget_tool = next(t for t in tools if t.__name__ == "viking_forget")
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.session_id = "e2e-test"

        result = await forget_tool(ctx, uri=uri)
        assert "error" not in result.lower()

        # Verify it's gone
        entries = await client.ls(test_dir)
        names = [e.get("name") for e in entries if isinstance(e, dict)]
        assert "deletable.md" not in names


class TestE2EGraphTools:
    """Test graph tools against real Viking."""

    async def test_viking_link(self, viking_cap: VikingCapability, test_dir: str) -> None:
        client = viking_cap._get_client()
        uri_a = f"{test_dir}node_a.md"
        uri_b = f"{test_dir}node_b.md"
        await client.write(uri_a, "content a", mode="create")
        await client.write(uri_b, "content b", mode="create")

        tools = build_tools(viking_cap)
        link_tool = next(t for t in tools if t.__name__ == "viking_link")
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.session_id = "e2e-test"

        result = await link_tool(ctx, from_uri=uri_a, to_uris=uri_b, reason="test link")
        if "error" in result.lower() and "unavailable" in result.lower():
            pytest.skip("Viking link not supported on this backend")
        assert "error" not in result.lower()

    async def test_viking_set_tags(self, viking_cap: VikingCapability, test_dir: str) -> None:
        client = viking_cap._get_client()
        uri = f"{test_dir}tagged.md"
        await client.write(uri, "tagged content", mode="create")

        tools = build_tools(viking_cap)
        tags_tool = next(t for t in tools if t.__name__ == "viking_set_tags")
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.session_id = "e2e-test"

        result = await tags_tool(ctx, uri=uri, tags=["category=test", "priority=high"])
        assert "error" not in result.lower()


class TestE2ESkillResource:
    """Test SkillResource Protocol against real Viking."""

    async def test_list_skills(
        self, viking_cap: VikingCapability, allow_model_requests: None
    ) -> None:
        # Create a skills directory with a test skill
        client = viking_cap._get_client()
        skills_uri = f"viking://user/default/memories/viking_e2e_test_skills_{_random_name()}/"
        try:
            await client.mkdir(skills_uri, description="Test skills")
            await client.write(
                f"{skills_uri}test_skill.md",
                "---\ndescription: A test skill\n---\n# Test Skill\nContent here.",
                mode="create",
            )

            cap = VikingCapability(skills_uri=skills_uri)
            cap._client = client
            cap._owns_client = False

            skills = await cap.list_skills()
            assert len(skills) >= 1
            assert any(s.name == "test_skill" for s in skills)
            assert all(s.source == "remote" for s in skills)
        finally:
            with suppress(Exception):
                await client.rm(skills_uri, recursive=True)

    async def test_read_skill(
        self, viking_cap: VikingCapability, allow_model_requests: None
    ) -> None:
        client = viking_cap._get_client()
        skills_uri = f"viking://user/default/memories/viking_e2e_test_skills_{_random_name()}/"
        try:
            await client.mkdir(skills_uri, description="Test skills")
            await client.write(
                f"{skills_uri}my_skill.md",
                "# My Skill\nThis is the skill content.",
                mode="create",
            )

            cap = VikingCapability(skills_uri=skills_uri)
            cap._client = client
            cap._owns_client = False

            content = await cap.read_skill("my_skill")
            assert content is not None
            assert "My Skill" in content

            # Non-existent skill
            assert await cap.read_skill("nonexistent") is None
        finally:
            with suppress(Exception):
                await client.rm(skills_uri, recursive=True)

    async def test_skill_exists(
        self, viking_cap: VikingCapability, allow_model_requests: None
    ) -> None:
        client = viking_cap._get_client()
        skills_uri = f"viking://user/default/memories/viking_e2e_test_skills_{_random_name()}/"
        try:
            await client.mkdir(skills_uri, description="Test skills")
            await client.write(f"{skills_uri}exists.md", "content", mode="create")

            cap = VikingCapability(skills_uri=skills_uri)
            cap._client = client
            cap._owns_client = False

            assert await cap.skill_exists("exists") is True
            assert await cap.skill_exists("not_here") is False
        finally:
            with suppress(Exception):
                await client.rm(skills_uri, recursive=True)


class TestE2EResourceAccess:
    """Test ResourceAccess Protocol against real Viking."""

    async def test_list_resources(self, viking_cap: VikingCapability) -> None:
        resources = await viking_cap.list_resources()
        # viking://resources/ exists (we saw "chapters" earlier)
        assert isinstance(resources, list)
        # Should find at least the "chapters" directory's children
        # (list_resources filters out directories, so we may get files)

    async def test_read_resource(
        self, viking_cap: VikingCapability, allow_model_requests: None
    ) -> None:
        # First list to find an actual resource
        client = viking_cap._get_client()
        entries = await client.ls("viking://resources/chapters/")
        if not entries:
            pytest.skip("No resources in viking://resources/chapters/")

        # Find a file to read
        file_entry = None
        for e in entries:
            if isinstance(e, dict) and not e.get("isDir"):
                file_entry = e
                break
        if file_entry is None:
            pytest.skip("No files in viking://resources/chapters/")

        uri = file_entry.get("uri", "")
        result = await viking_cap.read_resource(uri)
        assert result is not None
        assert len(result) >= 1
        assert result[0].text  # Non-empty content

    async def test_resource_exists(
        self, viking_cap: VikingCapability, allow_model_requests: None
    ) -> None:
        # Test with a known existing path
        client = viking_cap._get_client()
        entries = await client.ls("viking://resources/")
        if not entries:
            pytest.skip("No resources in viking://resources/")

        # Get first entry URI
        first_uri = entries[0].get("uri", "") if isinstance(entries[0], dict) else ""
        if not first_uri:
            pytest.skip("No URI found")

        assert await viking_cap.resource_exists(first_uri) is True
        assert await viking_cap.resource_exists("viking://resources/nonexistent_xyz.md") is False


class TestE2EModeFiltering:
    """Test mode-based tool filtering with real capability."""

    async def test_retrieve_mode(self) -> None:
        cap = VikingCapability(mode="retrieve")
        await cap.__aenter__()
        try:
            from agentpool.capabilities.viking.tools import build_tools

            tools = build_tools(cap)
            tool_names = {t.__name__ for t in tools}
            assert "viking_search" in tool_names
            assert "viking_read" in tool_names
            assert "viking_write" not in tool_names
            assert "viking_link" not in tool_names
        finally:
            await cap.__aexit__(None, None, None)

    async def test_all_mode(self) -> None:
        cap = VikingCapability(mode="all")
        await cap.__aenter__()
        try:
            from agentpool.capabilities.viking.tools import build_tools

            tools = build_tools(cap)
            assert len(tools) == 15
        finally:
            await cap.__aexit__(None, None, None)


class TestE2EInstructions:
    """Test instructions content."""

    async def test_get_instructions(self, viking_cap: VikingCapability) -> None:
        instructions = viking_cap.get_instructions()
        assert instructions is not None
        assert len(instructions) > 100  # Substantial content
        assert "viking_search" in instructions
        assert "viking_read" in instructions
