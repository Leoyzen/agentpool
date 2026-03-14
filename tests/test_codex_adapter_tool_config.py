"""Tests for Codex adapter tool configuration models."""

from __future__ import annotations

from pydantic import TypeAdapter

from codex_adapter.models.tool_config import (
    ApplyPatchToolConfig,
    BuiltinToolsConfig,
    CollabToolsConfig,
    GrepFilesToolConfig,
    ImageGenerationToolConfig,
    JsReplToolConfig,
    ListDirToolConfig,
    ReadFileToolConfig,
    RequestPermissionsToolConfig,
    ShellToolConfig,
    ToolConfig,
    ToolSuggestToolConfig,
    ViewImageToolConfig,
    WebSearchLocationConfig,
    WebSearchToolConfig,
    tools_to_config_dict,
)


# ===========================================================================
# BuiltinToolsConfig tests
# ===========================================================================


def test_default_config_produces_empty_dict():
    """Default BuiltinToolsConfig (all None) should produce an empty config dict."""
    config = BuiltinToolsConfig()
    result = config.to_config_dict()
    assert result == {}


def test_shell_config():
    """Including shell tool should set the feature flag."""
    config = BuiltinToolsConfig(shell=ShellToolConfig())
    result = config.to_config_dict()
    assert result["features"]["shell_tool"] is True


def test_shell_allow_login_shell():
    """Setting allow_login_shell should appear as a top-level config key."""
    config = BuiltinToolsConfig(shell=ShellToolConfig(allow_login_shell=False))
    result = config.to_config_dict()
    assert result["allow_login_shell"] is False


def test_apply_patch_config():
    """Including apply_patch should set include_apply_patch_tool to True."""
    config = BuiltinToolsConfig(apply_patch=ApplyPatchToolConfig())
    result = config.to_config_dict()
    assert result["include_apply_patch_tool"] is True


def test_web_search_mode():
    """Setting web search mode should appear in config."""
    config = BuiltinToolsConfig(web_search=WebSearchToolConfig(mode="live"))
    result = config.to_config_dict()
    assert result["web_search"] == "live"


def test_web_search_full_config():
    """Full web search config should populate tools section."""
    config = BuiltinToolsConfig(
        web_search=WebSearchToolConfig(
            mode="cached",
            context_size="high",
            allowed_domains=["example.com"],
            location=WebSearchLocationConfig(country="US", city="NYC"),
        ),
    )
    result = config.to_config_dict()
    assert result["web_search"] == "cached"
    tools = result["tools"]["web_search"]
    assert tools["context_size"] == "high"
    assert tools["allowed_domains"] == ["example.com"]
    assert tools["location"]["country"] == "US"
    assert tools["location"]["city"] == "NYC"


def test_view_image():
    """Including view_image should set tools.view_image to True."""
    config = BuiltinToolsConfig(view_image=ViewImageToolConfig())
    result = config.to_config_dict()
    assert result["tools"]["view_image"] is True


def test_js_repl():
    """Including js_repl should set the feature flag."""
    config = BuiltinToolsConfig(js_repl=JsReplToolConfig())
    result = config.to_config_dict()
    assert result["features"]["js_repl"] is True


def test_collab():
    """Including collab should set multi_agent feature to True."""
    config = BuiltinToolsConfig(collab=CollabToolsConfig())
    result = config.to_config_dict()
    assert result["features"]["multi_agent"] is True


def test_image_generation():
    """Including image generation should set the feature flag."""
    config = BuiltinToolsConfig(image_generation=ImageGenerationToolConfig())
    result = config.to_config_dict()
    assert result["features"]["image_generation"] is True


def test_request_permissions():
    """Including request_permissions should set the feature flag."""
    config = BuiltinToolsConfig(request_permissions=RequestPermissionsToolConfig())
    result = config.to_config_dict()
    assert result["features"]["request_permissions_tool"] is True


def test_experimental_tools():
    """Including experimental tools should populate experimental_supported_tools."""
    config = BuiltinToolsConfig(
        grep_files=GrepFilesToolConfig(),
        read_file=ReadFileToolConfig(),
        list_dir=ListDirToolConfig(),
    )
    result = config.to_config_dict()
    assert "grep_files" in result["experimental_supported_tools"]
    assert "read_file" in result["experimental_supported_tools"]
    assert "list_dir" in result["experimental_supported_tools"]


def test_tool_suggest():
    """Including tool_suggest should set the feature flag."""
    config = BuiltinToolsConfig(tool_suggest=ToolSuggestToolConfig())
    result = config.to_config_dict()
    assert result["features"]["tool_suggest"] is True


def test_combined_config():
    """Multiple tools configured together should produce correct combined config."""
    config = BuiltinToolsConfig(
        shell=ShellToolConfig(),
        web_search=WebSearchToolConfig(mode="live"),
        js_repl=JsReplToolConfig(),
        grep_files=GrepFilesToolConfig(),
    )
    result = config.to_config_dict()
    assert result["features"]["shell_tool"] is True
    assert result["features"]["js_repl"] is True
    assert result["web_search"] == "live"
    assert "grep_files" in result["experimental_supported_tools"]


def test_no_features_key_when_empty():
    """Config dict should not contain 'features' key when no feature flags are set."""
    config = BuiltinToolsConfig(
        web_search=WebSearchToolConfig(mode="cached"),
    )
    result = config.to_config_dict()
    assert "features" not in result
    assert result["web_search"] == "cached"


def test_no_experimental_key_when_empty():
    """Config dict should not have experimental_supported_tools when none set."""
    config = BuiltinToolsConfig(shell=ShellToolConfig())
    result = config.to_config_dict()
    assert "experimental_supported_tools" not in result


def test_to_tool_list_only_set():
    """to_tool_list should only return explicitly set tools."""
    config = BuiltinToolsConfig(shell=ShellToolConfig(), js_repl=JsReplToolConfig())
    tools = config.to_tool_list()
    assert len(tools) == 2
    assert isinstance(tools[0], ShellToolConfig)
    assert isinstance(tools[1], JsReplToolConfig)


def test_to_tool_list_empty_default():
    """Default BuiltinToolsConfig should produce an empty tool list."""
    config = BuiltinToolsConfig()
    assert config.to_tool_list() == []


def test_roundtrip_model_dump():
    """BuiltinToolsConfig should serialize and deserialize cleanly."""
    original = BuiltinToolsConfig(
        shell=ShellToolConfig(allow_login_shell=True),
        web_search=WebSearchToolConfig(
            mode="live",
            context_size="medium",
            location=WebSearchLocationConfig(country="DE"),
        ),
        js_repl=JsReplToolConfig(),
    )
    data = original.model_dump()
    restored = BuiltinToolsConfig.model_validate(data)
    assert original == restored
    assert original.to_config_dict() == restored.to_config_dict()


# ===========================================================================
# tools_to_config_dict (list-based API) tests
# ===========================================================================


def test_tools_list_empty_disables_all():
    """Empty list should disable all builtin tools."""
    result = tools_to_config_dict([])
    assert result["features"]["shell_tool"] is False
    assert result["include_apply_patch_tool"] is False
    assert result["web_search"] == "disabled"
    assert result["features"]["js_repl"] is False
    assert result["features"]["multi_agent"] is False
    assert result["features"]["image_generation"] is False


def test_tools_list_single():
    """Single tool config in list."""
    result = tools_to_config_dict([WebSearchToolConfig(mode="live")])
    assert result["web_search"] == "live"


def test_tools_list_multiple():
    """Multiple tool configs in list."""
    result = tools_to_config_dict([
        ShellToolConfig(),
        JsReplToolConfig(),
        GrepFilesToolConfig(),
    ])
    assert result["features"]["shell_tool"] is True
    assert result["features"]["js_repl"] is True
    assert "grep_files" in result["experimental_supported_tools"]


def test_tools_list_matches_builtin_config():
    """List-based and BuiltinToolsConfig should produce same output."""
    builtin = BuiltinToolsConfig(
        shell=ShellToolConfig(),
        web_search=WebSearchToolConfig(mode="live"),
        js_repl=JsReplToolConfig(),
    )
    list_result = tools_to_config_dict([
        ShellToolConfig(),
        WebSearchToolConfig(mode="live"),
        JsReplToolConfig(),
    ])
    assert builtin.to_config_dict() == list_result


def test_to_tool_list_roundtrips():
    """BuiltinToolsConfig.to_tool_list() should roundtrip through tools_to_config_dict."""
    config = BuiltinToolsConfig(
        collab=CollabToolsConfig(),
        image_generation=ImageGenerationToolConfig(),
    )
    from_list = tools_to_config_dict(config.to_tool_list())
    from_config = config.to_config_dict()
    assert from_list == from_config


# ===========================================================================
# Discriminated union tests
# ===========================================================================


def test_discriminator_resolves_type():
    """ToolConfig union should resolve based on type field."""
    adapter = TypeAdapter(ToolConfig)
    shell = adapter.validate_python({"type": "shell"})
    assert isinstance(shell, ShellToolConfig)


def test_discriminator_all_types():
    """Each tool type should be resolvable via the discriminator."""
    adapter = TypeAdapter(ToolConfig)
    types = [
        "shell",
        "apply_patch",
        "web_search",
        "image_generation",
        "view_image",
        "plan",
        "js_repl",
        "collab",
        "agent_jobs",
        "request_user_input",
        "request_permissions",
        "artifacts",
        "grep_files",
        "read_file",
        "list_dir",
        "tool_search",
        "tool_suggest",
        "mcp_resources",
    ]
    for type_name in types:
        result = adapter.validate_python({"type": type_name})
        assert result.type == type_name


def test_discriminated_list_roundtrip():
    """A list[ToolConfig] should serialize and deserialize with discriminator."""
    adapter = TypeAdapter(list[ToolConfig])
    tools: list[ToolConfig] = [
        ShellToolConfig(),
        WebSearchToolConfig(mode="live"),
        JsReplToolConfig(),
    ]
    data = adapter.dump_python(tools, mode="python")
    restored = adapter.validate_python(data)
    assert len(restored) == 3
    assert isinstance(restored[0], ShellToolConfig)
    assert isinstance(restored[1], WebSearchToolConfig)
    assert isinstance(restored[2], JsReplToolConfig)


def test_discriminated_list_json_roundtrip():
    """list[ToolConfig] should roundtrip through JSON."""
    adapter = TypeAdapter(list[ToolConfig])
    tools: list[ToolConfig] = [
        ShellToolConfig(allow_login_shell=True),
        WebSearchToolConfig(mode="cached", context_size="high"),
    ]
    json_bytes = adapter.dump_json(tools)
    restored = adapter.validate_json(json_bytes)
    assert len(restored) == 2
    assert isinstance(restored[0], ShellToolConfig)
    assert restored[0].allow_login_shell is True
    assert isinstance(restored[1], WebSearchToolConfig)
    assert restored[1].context_size == "high"
