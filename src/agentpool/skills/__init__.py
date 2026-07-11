"""Skills package for Claude Code Skills support."""

from agentpool.skills.manager import SkillsManager
from agentpool.skills.skill import Skill, to_prompt

__all__ = ["Skill", "SkillsManager", "to_prompt"]
