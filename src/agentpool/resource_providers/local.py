"""Local filesystem resource provider for skills."""

from __future__ import annotations

import mimetypes
from typing import TYPE_CHECKING, Self

from cachetools import TTLCache
from upathtools import UPath

from agentpool.resource_providers.base import ResourceProvider
from agentpool.skills.exceptions import (
    ReferenceNotFoundError,
    SecurityError,
    SkillNotFoundError,
)
from agentpool.skills.registry import SkillsRegistry


if TYPE_CHECKING:
    from types import TracebackType

    from upathtools import JoinablePathLike

    from agentpool.skills.skill import Skill


class LocalResourceProvider(ResourceProvider):
    """Resource provider for local filesystem skills.

    Discovers skills from filesystem directories using SkillsRegistry,
    provides caching with TTL, and serves skill instructions and references.

    Attributes:
        kind: Provider type identifier ("local")
        name: Unique name for this provider instance
        skills_dirs: List of directories to scan for skills
        cache_ttl: Time-to-live for skill cache in seconds
    """

    kind = "custom"

    def __init__(
        self,
        name: str,
        skills_dirs: list[JoinablePathLike],
        owner: str | None = None,
        cache_ttl: float = 60.0,
    ) -> None:
        """Initialize the local resource provider.

        Args:
            name: Unique name for this provider instance
            skills_dirs: List of directories to scan for skills
            owner: Optional owner identifier (e.g., agent name)
            cache_ttl: Cache time-to-live in seconds (default: 60)
        """
        super().__init__(name=name, owner=owner)
        self.skills_dirs = [UPath(d).expanduser() for d in skills_dirs]
        self.cache_ttl = cache_ttl
        self._registry = SkillsRegistry(skills_dirs=self.skills_dirs)
        self._cache: TTLCache[str, Skill] = TTLCache(maxsize=1000, ttl=cache_ttl)
        self._cache_valid = False

    async def __aenter__(self) -> Self:
        """Async context entry - discover skills and connect callbacks.

        Returns:
            Self for context manager protocol
        """
        await self._registry.discover_skills()
        self._connect_registry_callbacks()
        self._cache_valid = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context cleanup - disconnect callbacks and clear cache."""
        self._registry._skill_added_handlers.clear()
        self._registry._skill_removed_handlers.clear()
        self._cache.clear()
        self._cache_valid = False

    async def get_skills(self) -> list[Skill]:
        """Get all available skills with caching.

        Returns cached skills if cache is valid, otherwise fetches from registry.
        Cache is invalidated when skills are added or removed.

        Returns:
            List of available skills
        """
        if self._cache_valid and len(self._cache) == len(self._registry):
            return list(self._cache.values())

        # Refresh cache from registry
        self._cache.clear()
        for name, skill in self._registry.items():
            self._cache[name] = skill

        self._cache_valid = True
        return list(self._cache.values())

    async def get_skill(self, name: str) -> Skill:
        """Get a specific skill by name.

        Args:
            name: Name of the skill to retrieve

        Returns:
            The requested skill

        Raises:
            SkillNotFoundError: If skill is not found
        """
        # Try cache first
        if name in self._cache:
            return self._cache[name]

        # Try registry
        try:
            skill = self._registry.get(name)
            self._cache[name] = skill
            return skill
        except Exception as e:
            available = list(self._registry.keys())
            raise SkillNotFoundError(name, available) from e

    async def get_skill_instructions(
        self, skill_name: str, arguments: dict[str, str] | None = None
    ) -> str:
        """Get full instructions for a specific skill.

        Args:
            skill_name: Name of the skill to get instructions for
            arguments: Optional arguments (ignored for local filesystem skills)

        Returns:
            The full skill instructions from SKILL.md

        Raises:
            SkillNotFoundError: If skill is not found
        """
        skill = await self.get_skill(skill_name)
        return skill.load_instructions()

    async def get_references(self, skill_name: str) -> list[str]:
        """List reference files available for a skill.

        Args:
            skill_name: Name of the skill to get references for

        Returns:
            List of reference file names (not full paths)

        Raises:
            SkillNotFoundError: If skill is not found
        """
        skill = await self.get_skill(skill_name)
        references_dir = skill.skill_path / "references"

        if not references_dir.exists():
            return []

        # List all files recursively in references directory
        refs = []
        for item in references_dir.rglob("*"):
            if item.is_file():
                # Get relative path from references directory
                rel_path = item.relative_to(references_dir)
                refs.append(str(rel_path))

        return sorted(refs)

    async def read_reference(self, skill_name: str, ref_path: str) -> tuple[bytes, str]:
        """Read a reference file for a skill.

        Args:
            skill_name: Name of the skill containing the reference
            ref_path: Relative path to the reference file within references/

        Returns:
            Tuple of (file content as bytes, MIME type)

        Raises:
            SkillNotFoundError: If skill is not found
            ReferenceNotFoundError: If reference file is not found
            SecurityError: If path traversal is detected
        """
        skill = await self.get_skill(skill_name)
        references_dir = skill.skill_path / "references"

        if not references_dir.exists():
            raise ReferenceNotFoundError(ref_path)

        # Validate ref_path to prevent directory traversal
        # Reject paths containing ".."
        if ".." in ref_path.split("/"):
            raise SecurityError(f"Path traversal detected in: {ref_path}")

        # Construct the full path and validate it's within references_dir
        try:
            target_path = references_dir / ref_path
            # Use relative_to to verify target is within references_dir
            target_path.resolve().relative_to(references_dir.resolve())
        except (ValueError, RuntimeError) as e:
            raise SecurityError(f"Invalid reference path: {ref_path}") from e

        if not target_path.exists():
            raise ReferenceNotFoundError(ref_path)

        if not target_path.is_file():
            raise ReferenceNotFoundError(ref_path)

        content = target_path.read_bytes()
        mime_type = self._detect_mime_type(target_path)

        return content, mime_type

    def _detect_mime_type(self, path: UPath) -> str:
        """Detect MIME type for a file path.

        Args:
            path: Path to the file

        Returns:
            MIME type string (defaults to application/octet-stream)
        """
        guess = mimetypes.guess_type(str(path))[0]
        return guess or "application/octet-stream"

    def _connect_registry_callbacks(self) -> None:
        """Connect SkillsRegistry callbacks to skills_changed signal.

        When skills are added or removed in the registry, invalidate the cache
        and emit the skills_changed signal.
        """

        def on_added(name: str, skill: Skill) -> None:
            self._invalidate_cache()
            # Emit signal asynchronously
            import asyncio

            event = self.create_change_event("skills")
            try:
                loop = asyncio.get_running_loop()
                task_ref = loop.create_task(self.skills_changed.emit(event))
            except RuntimeError:
                # No running loop - can't emit signal
                pass

        def on_removed(name: str, skill: Skill | None) -> None:
            self._invalidate_cache()
            # Emit signal asynchronously
            import asyncio

            event = self.create_change_event("skills")
            try:
                loop = asyncio.get_running_loop()
                task_ref = loop.create_task(self.skills_changed.emit(event))
            except RuntimeError:
                # No running loop - can't emit signal
                pass

        self._registry.on_skill_added(on_added)
        self._registry.on_skill_removed(on_removed)

    def _invalidate_cache(self) -> None:
        """Invalidate the skills cache.

        Marks cache as invalid so next get_skills() call refreshes from registry.
        """
        self._cache_valid = False
        self._cache.clear()
