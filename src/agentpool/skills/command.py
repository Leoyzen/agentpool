"""Skill command dataclass for protocol-agnostic command representation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from agentpool.skills.skill import Skill


@dataclass(frozen=True)
class SkillCommand:
    """A skill exposed as a slash command.

    This dataclass provides a protocol-agnostic representation of a skill
    as a command that can be invoked via slash command interfaces.

    Attributes:
        name: Command name (typically the skill name without prefix).
        description: Human-readable description of what the command does.
        skill: The underlying Skill instance containing full skill metadata.
        input_hint: Hint text shown to users about command arguments.
        category: Command category for grouping (default "skill").
        skill_uri: Optional skill:// URI for the skill (e.g., skill://local/my-skill).
    """

    name: str
    """Command name (typically the skill name without prefix)."""

    description: str
    """Human-readable description of what the command does."""

    skill: Skill
    """The underlying Skill instance containing full skill metadata."""

    input_hint: str = "Arguments for skill"
    """Hint text shown to users about command arguments."""

    category: str = "skill"
    """Command category for grouping (default "skill")."""

    skill_uri: str | None = None
    """Optional skill:// URI for the skill (e.g., skill://local/my-skill)."""

    @property
    def resolved_skill_uri(self) -> str:
        """Get the skill URI, generating from name if not explicitly set.

        Returns:
            The skill:// URI for this command.
        """
        return self.skill_uri or f"skill://local/{self.name}"

    def is_valid_input(self, input_text: str) -> tuple[bool, str | None]:
        """Validate input text for this command.

        Args:
            input_text: The input to validate.

        Returns:
            A tuple containing:
                - Boolean indicating if input is valid
                - Error message string if invalid, None if valid
        """
        if not input_text.strip():
            return False, "Input cannot be empty"
        return True, None
