from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from pydantic_ai import RunContext
import pytest
from upathtools import UPath

from agentpool import AgentPool, AgentsManifest, NativeAgentConfig
from agentpool_config.skills import SkillsConfig, SkillsInstructionConfig
from agentpool_config.toolsets import SkillsToolsetConfig


if TYPE_CHECKING:
    from pydantic_ai import Agent as PydanticAgent


@pytest.fixture
def temp_skills_dir(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    skill1_dir = skills_dir / "test-skill-1"
    skill1_dir.mkdir()
    (skill1_dir / "SKILL.md").write_text("""---
name: test-skill-1
description: Description for skill 1
---
Full instructions for skill 1.""")

    skill2_dir = skills_dir / "test-skill-2"
    skill2_dir.mkdir()
    (skill2_dir / "SKILL.md").write_text("""---
name: test-skill-2
description: Description for skill 2
---
Full instructions for skill 2.""")

    return skills_dir


@pytest.mark.integration
async def test_skills_injection_default_off(temp_skills_dir):
    """Test that skills injection is off by default."""
    # Default is mode="off"
    manifest = AgentsManifest(
        skills=SkillsConfig(paths=[UPath(temp_skills_dir)], include_default=False),
        agents={"test_agent": NativeAgentConfig(name="test_agent", model="test")},
    )

    async with AgentPool(manifest) as pool:
        agent = pool.get_agent("test_agent")

        agentlet: PydanticAgent[None, str] = await agent.get_agentlet(  # type: ignore[attr-defined]
            None, None, None
        )

        all_inst_texts = []
        ctx = agent.get_context()
        run_ctx = MagicMock(spec=RunContext)
        run_ctx.deps = ctx
        for inst in agentlet._instructions:
            if callable(inst):
                all_inst_texts.append(await inst(run_ctx))
            else:
                all_inst_texts.append(inst)

        combined_instructions = "\n".join(all_inst_texts)
        # Default is off, so skills should NOT be injected
        assert "<available-skills>" not in combined_instructions
        assert 'name="test-skill-1"' not in combined_instructions
        assert "Full instructions for skill 1." not in combined_instructions


@pytest.mark.integration
async def test_skills_injection_agent_override_full_when_global_off(temp_skills_dir):
    """Test agent-specific override to full mode when global is off."""
    manifest = AgentsManifest(
        skills=SkillsConfig(
            paths=[UPath(temp_skills_dir)],
            include_default=False,
            instruction=SkillsInstructionConfig(mode="off"),  # Global is off
        ),
        agents={
            "test_agent": NativeAgentConfig(
                name="test_agent",
                model="test",
                tools=[
                    SkillsToolsetConfig(injection_mode="full")  # Override to full
                ],
            )
        },
    )

    async with AgentPool(manifest) as pool:
        agent = pool.get_agent("test_agent")

        agentlet: PydanticAgent[None, str] = await agent.get_agentlet(  # type: ignore[attr-defined]
            None, None, None
        )

        all_inst_texts = []
        ctx = agent.get_context()
        run_ctx = MagicMock(spec=RunContext)
        run_ctx.deps = ctx
        for inst in agentlet._instructions:
            if callable(inst):
                all_inst_texts.append(await inst(run_ctx))
            else:
                all_inst_texts.append(inst)

        combined_instructions = "\n".join(all_inst_texts)
        assert "<available-skills>" in combined_instructions
        assert 'name="test-skill-1"' in combined_instructions
        assert "Full instructions for skill 1." in combined_instructions
