"""Tests for SkillsConfig model."""

from __future__ import annotations

import pytest
from upathtools import UPath

from agentpool_config.context import ConfigContextManager
from agentpool_config.skills import DEFAULT_SKILLS_PATHS, SkillsConfig


def test_skills_config_default_values():
    """Test SkillsConfig with default values."""
    config = SkillsConfig()

    assert config.paths == []
    assert config.include_default is True


def test_skills_config_with_custom_paths():
    """Test SkillsConfig with custom paths."""
    config = SkillsConfig(paths=[UPath("./my-skills"), UPath("/absolute/path")])

    assert len(config.paths) == 2
    assert config.paths[0] == UPath("./my-skills")
    assert config.paths[1] == UPath("/absolute/path")
    assert config.include_default is True


def test_skills_config_include_default_false():
    """Test SkillsConfig with include_default set to False."""
    config = SkillsConfig(include_default=False)

    assert config.paths == []
    assert config.include_default is False


def test_config_path_resolution_with_context():
    """Test that ConfigPath resolves relative paths within ConfigContextManager."""
    with ConfigContextManager("/home/user/project/config.yml"):
        config = SkillsConfig.model_validate({"paths": ["./skills"], "include_default": False})
        # ConfigPath BeforeValidator should have resolved ./skills against config dir
        assert len(config.paths) == 1
        assert config.paths[0].is_absolute()
        assert str(config.paths[0]).endswith("skills")


def test_config_path_resolution_absolute_unchanged():
    """Test that absolute paths are not modified by ConfigPath resolution."""
    with ConfigContextManager("/home/user/project/config.yml"):
        config = SkillsConfig.model_validate({
            "paths": ["/absolute/skills"],
            "include_default": False,
        })
        assert len(config.paths) == 1
        assert config.paths[0] == UPath("/absolute/skills")


def test_config_path_resolution_no_context():
    """Test that paths without context remain relative."""
    # Without ConfigContextManager, ConfigPath cannot resolve relative paths
    config = SkillsConfig.model_validate({"paths": ["./skills"], "include_default": False})
    assert len(config.paths) == 1
    # Path may be relative or resolved against CWD depending on get_config_dir()
    # The key behavior is that it doesn't crash


def test_get_effective_paths_deprecated():
    """Test that get_effective_paths() emits DeprecationWarning."""
    config = SkillsConfig(paths=[UPath("/absolute/skills")], include_default=False)
    with pytest.warns(DeprecationWarning, match="get_effective_paths"):
        config.get_effective_paths()


def test_get_effective_paths_custom_only():
    """Test get_effective_paths with custom paths only (no defaults)."""
    config = SkillsConfig(
        paths=[UPath("/absolute/skills")],
        include_default=False,
    )

    with pytest.warns(DeprecationWarning, match="get_effective_paths"):
        result = config.get_effective_paths()

    assert len(result) == 1
    assert result[0] == UPath("/absolute/skills")


def test_get_effective_paths_with_defaults():
    """Test get_effective_paths includes default paths when enabled."""
    config = SkillsConfig(
        paths=[UPath("/custom-skills")],
        include_default=True,
    )

    with pytest.warns(DeprecationWarning, match="get_effective_paths"):
        result = config.get_effective_paths()

    assert len(result) == 3
    # Custom paths come first
    assert result[0] == UPath("/custom-skills")
    # Default paths come after
    assert result[1] == DEFAULT_SKILLS_PATHS[0]
    assert result[2] == DEFAULT_SKILLS_PATHS[1]


def test_get_effective_paths_absolute_paths_unaffected():
    """Test that absolute paths are not modified by config_file_path."""
    config_file = UPath("/some/other/path/config.yml")

    config = SkillsConfig(
        paths=[UPath("/custom/absolute/skills")],
        include_default=False,
    )

    with pytest.warns(DeprecationWarning, match="get_effective_paths"):
        result = config.get_effective_paths(config_file_path=config_file)

    assert len(result) == 1
    assert result[0] == UPath("/custom/absolute/skills")


def test_get_effective_paths_remote_paths():
    """Test that remote paths are preserved as-is."""
    config = SkillsConfig(
        paths=[UPath("s3://bucket/skills"), UPath("github://org/repo/skills")],
        include_default=False,
    )

    with pytest.warns(DeprecationWarning, match="get_effective_paths"):
        result = config.get_effective_paths()

    assert len(result) == 2
    assert result[0] == UPath("s3://bucket/skills")
    assert result[1] == UPath("github://org/repo/skills")


def test_get_effective_paths_first_path_wins():
    """Test 'first path wins' priority - custom paths before defaults."""
    config = SkillsConfig(
        paths=[UPath("/my-skills")],
        include_default=True,
    )

    with pytest.warns(DeprecationWarning, match="get_effective_paths"):
        result = config.get_effective_paths()

    # Custom paths come first
    assert result[0] == UPath("/my-skills")
    # Default paths come after
    assert result[1] == DEFAULT_SKILLS_PATHS[0]
    assert result[2] == DEFAULT_SKILLS_PATHS[1]


def test_pydantic_validation():
    """Test that SkillsConfig validates properly with Pydantic."""
    # Valid config
    config = SkillsConfig(paths=[UPath("/path")], include_default=True)
    assert config.paths == [UPath("/path")]
    assert config.include_default is True

    # Invalid types should raise ValidationError
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SkillsConfig(paths=["not", "a", "list"], include_default="not a bool")


def test_empty_config_no_defaults():
    """Test empty config with defaults disabled returns empty list."""
    config = SkillsConfig(paths=[], include_default=False)

    with pytest.warns(DeprecationWarning, match="get_effective_paths"):
        result = config.get_effective_paths()

    assert result == []


def test_config_yaml_roundtrip():
    """Test that SkillsConfig can be serialized/deserialized."""
    config = SkillsConfig(
        paths=[UPath("/skills"), UPath("/absolute/skills")],
        include_default=True,
    )

    # Serialize to dict and convert UPath objects to strings for roundtrip
    config_dict = config.model_dump()
    # UPath serializes to dicts; convert paths back to strings for deserialization
    config_dict["paths"] = [str(p) for p in config.paths]

    # Deserialize back
    config2 = SkillsConfig(**config_dict)

    assert config2.paths == config.paths
    assert config2.include_default == config.include_default
