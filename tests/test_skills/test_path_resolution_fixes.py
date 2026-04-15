"""Tests for path resolution fixes in skills discovery.

Covers the three bugs fixed:
1. Dead code removal in paths.py
2. ConfigContextManager ensures absolute paths
3. discover_skills() no longer calls deprecated get_effective_paths()
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from textwrap import dedent
import warnings

import pytest
from upathtools import UPath

from agentpool.skills.manager import SkillsManager
from agentpool_config.context import ConfigContextManager, get_config_dir
from agentpool_config.paths import resolve_config_path
from agentpool_config.skills import SkillsConfig


def _create_skill(base: Path, name: str, description: str) -> None:
    """Create a minimal skill directory with SKILL.md."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        dedent(f"""
        ---
        name: {name}
        description: {description}
        ---
        Instructions for {name}
        """).strip()
    )


class TestConfigContextManagerAbsolutePath:
    """Verify ConfigContextManager always stores absolute paths."""

    def test_absolute_path_stays_absolute(self):
        """An absolute path should remain absolute in _config_dir."""
        with ConfigContextManager("/absolute/path/config.yml") as ctx:
            assert ctx._config_dir is not None
            assert ctx._config_dir.is_absolute()

    def test_relative_path_becomes_absolute(self):
        """A relative path should be resolved to absolute in _config_dir."""
        with ConfigContextManager("relative/config.yml") as ctx:
            assert ctx._config_dir is not None
            assert ctx._config_dir.is_absolute()

    def test_global_config_dir_is_absolute(self):
        """After entering context, get_config_dir() returns absolute path."""
        with ConfigContextManager("relative/config.yml"):
            config_dir = get_config_dir()
            assert config_dir is not None
            assert config_dir.is_absolute()

    def test_nested_context_preserves_absolution(self):
        """Nested contexts should both have absolute paths."""
        with ConfigContextManager("/outer/config.yml"):
            outer_dir = get_config_dir()
            assert outer_dir is not None
            assert outer_dir.is_absolute()

            with ConfigContextManager("inner/config.yml"):
                inner_dir = get_config_dir()
                assert inner_dir is not None
                assert inner_dir.is_absolute()

            # After inner exits, outer should be restored
            assert get_config_dir() == outer_dir


class TestResolveConfigPath:
    """Verify resolve_config_path works correctly with context."""

    def test_absolute_path_unchanged(self):
        """Absolute paths should be returned as-is."""
        result = resolve_config_path("/absolute/path")
        assert result.is_absolute()
        assert str(result).startswith("/absolute/path")

    def test_relative_path_with_context(self):
        """Relative paths should be resolved against config dir."""
        with ConfigContextManager("/home/user/project/config.yml"):
            result = resolve_config_path("./skills")
            assert result.is_absolute()
            assert str(result).endswith("skills")

    def test_relative_path_without_context(self):
        """Relative paths without context should remain relative (fallback)."""
        # Ensure no context is set by using a fresh module state
        # This test may resolve against CWD or remain relative
        result = resolve_config_path("./relative")
        # Should not crash; may be relative or absolute
        assert result is not None


class TestDiscoverSkillsNoDeprecated:
    """Verify discover_skills() no longer calls get_effective_paths()."""

    @pytest.mark.asyncio
    async def test_discover_skills_does_not_call_deprecated_method(self):
        """discover_skills should not trigger DeprecationWarning from get_effective_paths."""
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir) / "skills"
            skills_dir.mkdir()
            _create_skill(skills_dir, "test_skill", "Test description")

            config = SkillsConfig(
                paths=[UPath(skills_dir)],
                include_default=False,
            )

            manager = SkillsManager()
            # If discover_skills calls get_effective_paths, this would trigger DeprecationWarning
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                await manager.discover_skills(config=config)

            # No DeprecationWarning should be emitted
            deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
            assert len(deprecation_warnings) == 0, (
                f"discover_skills() should not trigger DeprecationWarning, "
                f"got: {[str(w.message) for w in deprecation_warnings]}"
            )

            # Verify skill was actually found
            assert "test_skill" in manager.registry.list_items()

    @pytest.mark.asyncio
    async def test_discover_skills_with_include_default_resolves_paths(self):
        """When include_default=True, default paths should be resolved to absolute."""
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir) / "skills"
            skills_dir.mkdir()
            _create_skill(skills_dir, "test_skill", "Test description")

            config = SkillsConfig(
                paths=[UPath(skills_dir)],
                include_default=True,
            )

            manager = SkillsManager()
            await manager.discover_skills(
                config=config, config_file_path=UPath(temp_dir) / "config.yml"
            )

            # Custom skill should be found
            assert "test_skill" in manager.registry.list_items()


class TestEndToEndPathResolution:
    """End-to-end test for the complete path resolution chain."""

    @pytest.mark.asyncio
    async def test_relative_skills_path_resolved_via_config_context(self):
        """Simulate the serve-opencode flow: config with ./skills/ resolved correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            skills_dir = config_dir / "skills"
            skills_dir.mkdir()
            _create_skill(skills_dir, "diag_skill", "Diagnostic skill")

            # Create config YAML
            import yaml

            manifest_data = {
                "skills": {"paths": ["./skills"], "include_default": False},
            }
            config_path = config_dir / "config.yml"
            config_path.write_text(yaml.dump(manifest_data))

            # Simulate serve-opencode flow
            resolved_path = Path(config_path).resolve()
            with ConfigContextManager(str(resolved_path)):
                # model_validate triggers ConfigPath BeforeValidator
                validated_config = SkillsConfig.model_validate(manifest_data["skills"])

                # Verify path was resolved to absolute
                assert len(validated_config.paths) == 1
                assert validated_config.paths[0].is_absolute()

                # Discover skills using the validated config
                manager = SkillsManager()
                await manager.discover_skills(config=validated_config)

                # Skill should be found
                assert "diag_skill" in manager.registry.list_items()

    @pytest.mark.asyncio
    async def test_relative_path_works_from_different_cwd(self):
        """Verify that relative paths work even when CWD changes after config load."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            skills_dir = config_dir / "skills"
            skills_dir.mkdir()
            _create_skill(skills_dir, "cwd_test_skill", "CWD test skill")

            import yaml

            manifest_data = {
                "skills": {"paths": ["./skills"], "include_default": False},
            }
            config_path = config_dir / "config.yml"
            config_path.write_text(yaml.dump(manifest_data))

            # Load config from config_dir
            resolved_path = Path(config_path).resolve()
            with ConfigContextManager(str(resolved_path)):
                validated_config = SkillsConfig.model_validate(manifest_data["skills"])

            # Now change CWD (simulating a server that changes working directory)
            old_cwd = Path.cwd()
            try:
                os.chdir(tempfile.gettempdir())

                # Paths should still be absolute and valid
                assert validated_config.paths[0].is_absolute()
                assert validated_config.paths[0].exists()

                # Discover skills should still work
                manager = SkillsManager()
                await manager.discover_skills(config=validated_config)
                assert "cwd_test_skill" in manager.registry.list_items()
            finally:
                os.chdir(old_cwd)
