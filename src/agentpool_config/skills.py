"""Skills configuration."""

from __future__ import annotations

from pydantic import ConfigDict, Field
from schemez import Schema
from upathtools import UPath


DEFAULT_SKILLS_PATHS = [
    UPath("~/.claude/skills/"),
    UPath(".claude/skills/"),
]


class SkillsConfig(Schema):
    """Configuration for custom skill discovery paths.

    Skills are discovered from configured directories, allowing
    users to add custom skills from local paths. The discovery
    follows "first path wins" semantics - earlier paths in the list
    take precedence over later ones.

    Default paths (when include_default=True):
    - ~/.claude/skills/ (user home directory)
    - .claude/skills/ (relative to current directory)
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-icon": "octicon:mortar-board-16",
            "x-doc-title": "Skills Configuration",
        }
    )

    paths: list[UPath] = Field(
        default_factory=list,
        title="Custom skill paths",
        examples=[["/path/to/skills", "./my-skills", "s3://bucket/skills"]],
    )
    """List of custom paths to search for skills.

    Paths can be:
    - Absolute: /home/user/skills
    - Relative: ./my-skills (resolved against config file location or CWD)
    - Remote: s3://bucket/skills, github://org/repo/skills

    Earlier paths take precedence over later ones ("first path wins").
    """

    include_default: bool = Field(
        default=True,
        title="Include default paths",
        examples=[True, False],
    )
    """Whether to include default skill paths in discovery.

    Default paths are appended after custom paths:
    - ~/.claude/skills/
    - .claude/skills/

    Set to False to disable default paths entirely.
    """

    def get_effective_paths(self, config_file_path: UPath | None = None) -> list[UPath]:
        """Get the effective list of paths for skill discovery.

        Resolves relative paths against the config file location (if provided)
        or current working directory, then appends default paths if enabled.

        Args:
            config_file_path: Path to the YAML configuration file.
                Relative paths in self.paths are resolved against this file's
                parent directory. If None, relative paths are resolved against
                the current working directory.

        Returns:
            List of UPath objects for skill discovery, ordered by priority
            (custom paths first, then default paths if enabled).
        """
        result: list[UPath] = []

        # Resolve custom paths
        base_path = config_file_path.parent if config_file_path is not None else UPath.cwd()

        for path in self.paths:
            if path.is_absolute():
                result.append(path)
            else:
                # Resolve relative paths against base path and normalize
                result.append((base_path / path).resolve())

        # Append default paths if enabled
        if self.include_default:
            result.extend(DEFAULT_SKILLS_PATHS)

        return result
