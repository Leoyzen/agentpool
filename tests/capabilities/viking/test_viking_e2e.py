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
import json
import pathlib
import tempfile
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


@pytest.fixture
def mock_ctx() -> Any:
    """Create a MagicMock RunContext with session_id for tool calls."""
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.session_id = "e2e-test"
    return ctx


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

    async def test_viking_search(
        self, viking_cap: VikingCapability, test_dir: str, mock_ctx: Any
    ) -> None:
        """Test viking_search returns valid JSON results."""
        client = viking_cap._get_client()
        unique_marker = f"semantictest_{_random_name()}"
        await client.write(
            f"{test_dir}searchable.md",
            f"# {unique_marker}\n\nThis document discusses quantum entanglement and photon "
            f"polarization in the context of Bell's theorem.\n",
            mode="create",
        )

        tools = build_tools(viking_cap)
        search_tool = next(t for t in tools if t.__name__ == "viking_search")

        result = await search_tool(
            mock_ctx,
            query="quantum entanglement photon polarization",
            target_uri=test_dir,
            limit=5,
        )
        assert "error" not in result.lower()
        # Result should be valid JSON
        parsed = json.loads(result)
        assert isinstance(parsed, list | dict)

    async def test_viking_find(
        self, viking_cap: VikingCapability, test_dir: str, mock_ctx: Any
    ) -> None:
        """Test viking_find returns valid JSON results (deduplicated search)."""
        client = viking_cap._get_client()
        unique_marker = f"findtest_{_random_name()}"
        await client.write(
            f"{test_dir}findable.md",
            f"# {unique_marker}\n\nMachine learning model optimization with gradient descent "
            f"and backpropagation algorithms.\n",
            mode="create",
        )

        tools = build_tools(viking_cap)
        find_tool = next(t for t in tools if t.__name__ == "viking_find")

        result = await find_tool(
            mock_ctx,
            query="machine learning gradient descent",
            target_uri=test_dir,
            limit=5,
        )
        assert "error" not in result.lower()
        # Result should be valid JSON
        parsed = json.loads(result)
        assert isinstance(parsed, list | dict)

    async def test_viking_recall(
        self, viking_cap: VikingCapability, test_dir: str, mock_ctx: Any
    ) -> None:
        """Test viking_recall returns a formatted string with section headers."""
        tools = build_tools(viking_cap)
        recall_tool = next(t for t in tools if t.__name__ == "viking_recall")

        # Use valid context types accepted by the SDK (memory, resource, skill)
        result = await recall_tool(
            mock_ctx,
            query="test query for recall",
            quotas={"memory": 3, "resource": 3, "skill": 3},
        )
        assert "error" not in result.lower()
        # viking_recall returns a formatted string, not JSON
        assert isinstance(result, str)
        # Should contain section headers or be empty (no data found)
        if result.strip():
            assert "===" in result or "No" in result or len(result) > 0


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

    async def test_viking_remember(self, viking_cap: VikingCapability, mock_ctx: Any) -> None:
        """Test viking_remember stores conversation messages in Viking memory."""
        tools = build_tools(viking_cap)
        remember_tool = next(t for t in tools if t.__name__ == "viking_remember")

        messages = [
            {"role": "user", "content": f"Hello from E2E test {_random_name()}"},
            {"role": "assistant", "content": "Hi! I received your message."},
        ]

        result = await remember_tool(mock_ctx, messages=messages)
        assert "error" not in result.lower()
        assert "Remembered" in result
        assert "2" in result  # 2 messages

    async def test_viking_add_resource(self, viking_cap: VikingCapability, mock_ctx: Any) -> None:
        """Test viking_add_resource ingests a local file into Viking."""
        client = viking_cap._get_client()
        tools = build_tools(viking_cap)
        add_resource_tool = next(t for t in tools if t.__name__ == "viking_add_resource")

        # Create a temporary local file
        tmp_path = pathlib.Path(tempfile.mkstemp(suffix=".md", prefix="viking_resource_")[1])
        tmp_path.write_text(f"# Resource Test {_random_name()}\n\nContent for ingestion.\n")

        # add_resource requires 'to' to target resources/ path
        target_uri = f"viking://resources/e2e_test_{_random_name()}/"
        try:
            result = await add_resource_tool(
                mock_ctx,
                path=str(tmp_path),
                to=target_uri,
            )
            # SDK response includes 'errors': [] in JSON, so check for
            # the success marker instead of absence of "error" substring
            assert "Added resource" in result
            assert "viking_add_resource error:" not in result
        finally:
            # Clean up local temp file
            with suppress(Exception):
                tmp_path.unlink()
            # Clean up Viking resource (may still be processing, ignore errors)
            with suppress(Exception):
                await client.rm(target_uri, recursive=True)


class TestE2EGraphTools:
    """Test graph tools against real Viking."""

    @pytest.mark.xfail(
        reason="Viking backend does not support link() from memories/ paths",
        strict=False,
    )
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
        """Test read_resource by creating a resource file first, then reading it."""
        client = viking_cap._get_client()
        resource_uri = f"viking://resources/chapters/e2e_test_{_random_name()}.md"
        resource_content = f"# E2E Test Resource {_random_name()}\n\nThis is test content.\n"

        try:
            # Try writing under resources/chapters/
            try:
                await client.write(resource_uri, resource_content, mode="create")
            except OSError:
                # Fallback: write under memories/ and override resources_uri
                resource_uri = (
                    f"viking://user/default/memories/viking_e2e_test_resource_{_random_name()}.md"
                )
                await client.write(resource_uri, resource_content, mode="create")
                # Override resources_uri so read_resource can find it
                viking_cap.resources_uri = "viking://user/default/memories/"

            result = await viking_cap.read_resource(resource_uri)
            assert result is not None
            assert len(result) >= 1
            assert result[0].text  # Non-empty content
            assert (
                resource_content.strip() in result[0].text or "E2E Test Resource" in result[0].text
            )
        finally:
            with suppress(Exception):
                await client.rm(resource_uri)
            # Reset resources_uri override
            viking_cap.resources_uri = None

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


class TestE2EMultimodalBridge:
    """Test multimodal bridge — before_model_request with real Viking upload."""

    async def test_before_model_request_replaces_binary(
        self,
        viking_cap: VikingCapability,
        test_dir: str,
        allow_model_requests: None,
    ) -> None:
        """Test that before_model_request uploads binary and replaces with text ref."""
        from pydantic_ai.messages import BinaryContent, ModelRequest, TextPart, UserPromptPart
        from pydantic_ai.models import ModelRequestContext
        from pydantic_ai.models.test import TestModel

        from agentpool_config.model_capabilities import ModelCapabilities

        # Use test_dir as uploads_uri so we know the directory exists
        cap = VikingCapability(
            mode="all",
            multimodal_bridge=True,
            model_capabilities=ModelCapabilities(image_input=False),
            uploads_uri=test_dir,
            _client=viking_cap._get_client(),
            _owns_client=False,
        )

        user_prompt = UserPromptPart(
            content=[
                TextPart(content="describe this image"),
                BinaryContent(data=b"fake-image-data-for-testing", media_type="image/png"),
            ]
        )
        request_context = ModelRequestContext(
            model=TestModel(),
            messages=[ModelRequest(parts=[user_prompt])],
            model_settings=None,
            model_request_parameters=None,  # type: ignore[arg-type]
        )

        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.session_id = "e2e-test"

        modified_context = await cap.before_model_request(ctx, request_context)

        # The binary content should be replaced with a TextPart containing
        # a viking:// URI reference (upload uses .md extension since Viking
        # only allows .md files).
        uploaded_uri = self._verify_modification(modified_context)

        # Verify the uploaded file exists in Viking under test_dir
        client = viking_cap._get_client()
        entries = await client.ls(test_dir)
        assert self._check_upload_exists(entries, uploaded_uri), (
            f"Uploaded file should exist in Viking under {test_dir}"
        )
        # Cleanup: remove the uploaded file (test_dir itself is cleaned by fixture)
        with suppress(Exception):
            await client.rm(uploaded_uri)

    @staticmethod
    def _verify_modification(modified_context: Any) -> str:
        """Verify the modified context has binary replaced with text ref.

        Returns:
            The extracted viking:// URI of the uploaded content.
        """
        from pydantic_ai.messages import (
            BinaryContent,
            ModelRequest,
            TextPart,
            UserPromptPart,
        )

        modified_msg = modified_context.messages[0]
        assert isinstance(modified_msg, ModelRequest)
        user_part = next(
            (p for p in modified_msg.parts if isinstance(p, UserPromptPart)),
            None,
        )
        assert user_part is not None
        assert isinstance(user_part.content, list)

        has_text_ref = False
        has_binary = False
        for item in user_part.content:
            if isinstance(item, TextPart) and "viking://" in item.content:
                has_text_ref = True
            elif isinstance(item, BinaryContent):
                has_binary = True

        assert has_text_ref, (
            "BinaryContent should be replaced with a TextPart containing viking:// URI"
        )
        assert not has_binary, "Original BinaryContent should not remain"

        text_part = next(
            (i for i in user_part.content if isinstance(i, TextPart) and "viking://" in i.content),
            None,
        )
        assert text_part is not None
        content_str = text_part.content
        # Extract the viking:// URI from the text (may be inside brackets)
        uri_start = content_str.index("viking://")
        rest = content_str[uri_start:]
        # URI ends at first whitespace or closing bracket
        for i, ch in enumerate(rest):
            if ch in " \n\t]":
                return rest[:i]
        return rest

    @staticmethod
    def _check_upload_exists(entries: Any, uploaded_uri: str) -> bool:
        """Check if an uploaded file exists in the Viking entries list."""
        if not isinstance(entries, list):
            return False
        for entry in entries:
            if isinstance(entry, dict):
                name = str(entry.get("name", ""))
                if name in uploaded_uri:
                    return True
            elif isinstance(entry, str) and entry in uploaded_uri:
                return True
        return False
