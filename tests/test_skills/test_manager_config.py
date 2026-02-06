"""Tests for SkillsManager configuration-based discovery."""

from __future__ import annotations

import logging
from pathlib import Path
import tempfile
from textwrap import dedent

import pytest
from upathtools import UPath

from agentpool.skills.manager import SkillsManager
from agentpool_config.skills import SkillsConfig


@pytest.fixture
def skill_dirs():
    """Create two temporary directories with conflicting test skills."""
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        dir_a = base / "dir_a"
        dir_b = base / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()

        # Skill in dir_a
        skill_a = dir_a / "my_skill"
        skill_a.mkdir()
        (skill_a / "SKILL.md").write_text(
            dedent("""
            ---
            name: my_skill
            description: Description from A
            ---
            Instructions A
            """).strip()
        )

        # Same skill name in dir_b
        skill_b = dir_b / "my_skill"
        skill_b.mkdir()
        (skill_b / "SKILL.md").write_text(
            dedent("""
            ---
            name: my_skill
            description: Description from B
            ---
            Instructions B
            """).strip()
        )

        yield dir_a, dir_b


@pytest.mark.asyncio
async def test_discover_skills_priority(skill_dirs: tuple[Path, Path]):
    """Test that the first path in the config takes precedence (first path wins)."""
    dir_a, dir_b = skill_dirs
    # config.paths = [dir_a, dir_b] -> A should win because it's processed LAST in reversed list
    config = SkillsConfig(paths=[UPath(dir_a), UPath(dir_b)], include_default=False)

    manager = SkillsManager()
    await manager.discover_skills(config=config)

    skill = manager.get_skill("my_skill")
    assert skill.description == "Description from A"

    # Now swap priority: [dir_b, dir_a] -> B should win
    config_swapped = SkillsConfig(paths=[UPath(dir_b), UPath(dir_a)], include_default=False)
    manager_swapped = SkillsManager()
    await manager_swapped.discover_skills(config=config_swapped)

    skill_swapped = manager_swapped.get_skill("my_skill")
    assert skill_swapped.description == "Description from B"


@pytest.mark.asyncio
async def test_discover_skills_no_config(skill_dirs: tuple[Path, Path]):
    """Test discovery without a config object."""
    dir_a, _ = skill_dirs
    manager = SkillsManager(skills_dirs=[dir_a])
    await manager.discover_skills()

    skill = manager.get_skill("my_skill")
    assert skill.description == "Description from A"


@pytest.mark.asyncio
async def test_discover_skills_logging(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """Test that missing custom paths log WARNING and missing default paths log DEBUG."""
    from agentpool_config import skills

    mock_default = [UPath("/non/existent/default/path")]
    monkeypatch.setattr(skills, "DEFAULT_SKILLS_PATHS", mock_default)

    config = SkillsConfig(paths=[UPath("/non/existent/custom/path")], include_default=True)

    manager = SkillsManager()
    with caplog.at_level(logging.DEBUG):
        await manager.discover_skills(config=config)

    # Check for WARNING for custom path
    assert any(
        "Custom skills directory not found" in record.message and record.levelno == logging.WARNING
        for record in caplog.records
    )

    # Check for DEBUG for default paths
    assert any(
        "Default skills directory not found" in record.message and record.levelno == logging.DEBUG
        for record in caplog.records
    )

    # Check for DEBUG for default paths (they likely don't exist in the test environment)
    print(f"Logged messages: {[r.message for r in caplog.records]}")
    assert any(
        "Default skills directory not found" in record.message and record.levelno == logging.DEBUG
        for record in caplog.records
    )
