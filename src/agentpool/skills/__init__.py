"""Skills package for Claude Code Skills support."""

from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.manager import SkillsManager
from agentpool.skills.skill import Skill, to_prompt

__all__ = ["Skill", "SkillCommand", "SkillCommandRegistry", "SkillsManager", "to_prompt"]
