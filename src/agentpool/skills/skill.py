"""Claude Code Skill following the Agent Skills Spec."""

from __future__ import annotations

import html
from typing import Annotated, Any
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from upathtools import UPath


MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_COMPATIBILITY_LENGTH = 500


class Skill(BaseModel):
    """A Claude Code Skill with metadata and lazy-loaded instructions.

    Follows the Agent Skills Spec for field naming and validation rules.
    Frontmatter fields use ``extra="forbid"`` so unknown YAML keys are rejected.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: Annotated[str, Field(max_length=MAX_SKILL_NAME_LENGTH)]
    description: Annotated[str, Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)]
    skill_path: UPath
    license: str | None = None
    compatibility: Annotated[str | None, Field(max_length=MAX_COMPATIBILITY_LENGTH)] = None
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")
    metadata: dict[str, str] = Field(default_factory=dict)
    instructions: str | None = Field(default=None, exclude=True)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        name = unicodedata.normalize("NFKC", v.strip())
        if not name:
            raise ValueError("Skill name must be non-empty")
        if name != name.lower():
            raise ValueError(f"Skill name {name!r} must be lowercase")
        if name.startswith("-") or name.endswith("-"):
            raise ValueError("Skill name cannot start or end with a hyphen")
        if "--" in name:
            raise ValueError("Skill name cannot contain consecutive hyphens")
        if not all(c.isalnum() or c == "-" for c in name):
            msg = (
                f"Skill name {name!r} contains invalid characters. "
                "Only letters, digits, and hyphens are allowed."
            )
            raise ValueError(msg)
        return name

    @model_validator(mode="before")
    @classmethod
    def _normalize_metadata(cls, data: Any) -> Any:
        if isinstance(data, dict) and "metadata" in data:
            meta = data["metadata"]
            if isinstance(meta, dict):
                data["metadata"] = {str(k): str(v) for k, v in meta.items()}
        return data

    def load_instructions(self) -> str:
        """Lazy-load full instructions from SKILL.md."""
        if self.instructions is None:
            skill_file = self.skill_path / "SKILL.md"
            if skill_file.exists():
                content = skill_file.read_text(encoding="utf-8")
                parts = content.split("---", 2)
                if len(parts) >= 3:  # noqa: PLR2004
                    self.instructions = parts[2].strip()
                else:
                    self.instructions = ""
            else:
                self.instructions = ""
        return self.instructions

    @classmethod
    def from_skill_dir(cls, skill_dir: UPath) -> Skill:
        """Parse a SKILL.md file from a directory and return a validated Skill.

        Args:
            skill_dir: Path to the skill directory containing SKILL.md.

        Returns:
            Validated Skill instance.

        Raises:
            FileNotFoundError: If SKILL.md is not found.
            ValueError: If frontmatter is missing or invalid YAML.
            ValidationError: If fields fail Pydantic validation.
        """
        skill_file = find_skill_md(skill_dir)
        if skill_file is None:
            msg = f"SKILL.md not found in {skill_dir}"
            raise FileNotFoundError(msg)

        content = skill_file.read_text("utf-8")
        metadata, _body = parse_frontmatter(content)
        return cls(skill_path=skill_dir, **metadata)


def find_skill_md(skill_dir: UPath) -> UPath | None:
    """Find the SKILL.md file in a skill directory.

    Prefers SKILL.md (uppercase) but accepts skill.md (lowercase).

    Args:
        skill_dir: Path to the skill directory.

    Returns:
        Path to the SKILL.md file, or None if not found.
    """
    for name in ("SKILL.md", "skill.md"):
        path = skill_dir / name
        if path.exists():
            return path
    return None


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from SKILL.md content.

    Args:
        content: Raw content of a SKILL.md file.

    Returns:
        Tuple of (metadata dict, markdown body).

    Raises:
        ValueError: If frontmatter is missing or invalid.
    """
    if not content.startswith("---"):
        msg = "SKILL.md must start with YAML frontmatter (---)"
        raise ValueError(msg)

    parts = content.split("---", 2)
    if len(parts) < 3:  # noqa: PLR2004
        msg = "SKILL.md frontmatter not properly closed with ---"
        raise ValueError(msg)

    import yamling

    try:
        metadata = yamling.load_yaml(parts[1])
    except yamling.YAMLError as e:
        msg = f"Invalid YAML in frontmatter: {e}"
        raise ValueError(msg) from e

    if not isinstance(metadata, dict):
        msg = "SKILL.md frontmatter must be a YAML mapping"
        raise TypeError(msg)

    return metadata, parts[2].strip()


def to_prompt(skills: list[Skill]) -> str:
    """Generate the ``<available_skills>`` XML block for agent system prompts.

    This XML format is what Anthropic uses and recommends for Claude models.

    Args:
        skills: List of skills to include.

    Returns:
        XML string with ``<available_skills>`` block.
    """
    if not skills:
        return "<available_skills>\n</available_skills>"

    lines = ["<available_skills>"]
    for skill in skills:
        lines.append("<skill>")
        lines.append(f"<name>{html.escape(skill.name)}</name>")
        lines.append(f"<description>{html.escape(skill.description)}</description>")
        skill_md = find_skill_md(skill.skill_path)
        if skill_md is not None:
            lines.append(f"<location>{skill_md}</location>")
        lines.append("</skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)
