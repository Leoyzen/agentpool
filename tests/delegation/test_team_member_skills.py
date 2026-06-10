from __future__ import annotations

import asyncio

from agentpool import Team


class _SkillProvider:
    async def get_skill_instructions(self, skill_name: str) -> str:
        return f"# {skill_name}\nUse this skill."


class _Pool:
    skill_provider = _SkillProvider()
    skills = None


def test_team_loads_member_skills_from_pool_provider() -> None:
    team = Team([], name="review_team")
    team.agent_pool = _Pool()

    result = asyncio.run(team._load_member_skill_instructions({
        "root_cause_reviewer": ["fta-causal-path-review"],
    }))

    assert "<skill-instruction name=\"fta-causal-path-review\">" in result["root_cause_reviewer"]
    assert "Use this skill." in result["root_cause_reviewer"]


def test_team_injects_member_skills_into_member_prompt() -> None:
    prompt = Team._inject_member_skill_instructions(
        "root_cause_reviewer",
        ["Review the FTA."],
        {"root_cause_reviewer": "<skill-instruction>Skill body</skill-instruction>"},
    )

    assert prompt == ["<skill-instruction>Skill body</skill-instruction>\n\nReview the FTA."]
