"""URI resolver for the skill:// scheme.

Provides secure parsing and resolution of skill URIs with support for:
- Explicit providers: skill://provider/skill-name
- Reference paths: skill://provider/skill-name/references/file.md
- Bare skill names: skill-name

Security features:
- Path traversal detection (rejects ".." in paths)
- Null byte detection
- Provider name validation
- Skill name validation (follows Skill model rules)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from agentpool.skills.exceptions import SecurityError, SkillNotFoundError

if TYPE_CHECKING:
    from agentpool.resource_providers.base import ResourceProvider
    from agentpool.skills.skill import Skill


# Maximum length for provider names (DNS label compatible)
MAX_PROVIDER_NAME_LENGTH = 63

# Valid provider name pattern: alphanumeric, hyphen, underscore
PROVIDER_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class ResolvedSkillURI:
    """A resolved and validated skill:// URI.

    Attributes:
        provider: Provider name or None for bare skill names
        skill_name: The validated skill name
        reference_path: Optional path to reference file within skill

    Examples:
        >>> ResolvedSkillURI.parse("skill://local/python-expert")
        ResolvedSkillURI(provider='local', skill_name='python-expert', reference_path=None)

        >>> ResolvedSkillURI.parse("skill://local/python-expert/references/guide.md")
        ResolvedSkillURI(provider='local', skill_name='python-expert', reference_path='references/guide.md')

        >>> ResolvedSkillURI.parse("python-expert")
        ResolvedSkillURI(provider=None, skill_name='python-expert', reference_path=None)
    """

    provider: str | None
    skill_name: str
    reference_path: str | None

    @classmethod
    def parse(cls, uri: str) -> ResolvedSkillURI:
        """Parse and validate a skill URI.

        Supports three formats:
        1. skill://provider/skill-name - With explicit provider
        2. skill://provider/skill-name/path - With reference path
        3. skill-name - Bare skill name (provider=None)

        Args:
            uri: The URI to parse

        Returns:
            ResolvedSkillURI with validated components

        Raises:
            SecurityError: If path traversal, null bytes, or invalid characters detected
            ValueError: If URI format is invalid

        Examples:
            >>> ResolvedSkillURI.parse("skill://local/my-skill")
            ResolvedSkillURI(provider='local', skill_name='my-skill', reference_path=None)

            >>> ResolvedSkillURI.parse("my-skill")
            ResolvedSkillURI(provider=None, skill_name='my-skill', reference_path=None)
        """
        # Check for null bytes first
        if "\x00" in uri:
            msg = "URI contains null bytes"
            raise SecurityError(msg)

        # URL decode the entire URI first
        decoded_uri = unquote(uri)

        # Check for null bytes after decoding
        if "\x00" in decoded_uri:
            msg = "URI contains null bytes after decoding"
            raise SecurityError(msg)

        # Check if it's a bare skill name (no scheme)
        if "://" not in decoded_uri:
            # Validate as bare skill name
            skill_name = _validate_skill_name(decoded_uri)
            return cls(provider=None, skill_name=skill_name, reference_path=None)

        # Parse as full URI
        parsed = urlparse(decoded_uri)

        # Validate scheme
        if parsed.scheme != "skill":
            msg = f"Invalid URI scheme: {parsed.scheme!r}, expected 'skill'"
            raise ValueError(msg)

        # Extract and validate provider from netloc
        provider = parsed.netloc if parsed.netloc else None
        if provider is not None:
            provider = _validate_provider_name(provider)

        # Parse the path component
        path = parsed.path
        if path.startswith("/"):
            path = path[1:]  # Remove leading slash

        if not path:
            msg = "URI path is empty"
            raise ValueError(msg)

        # Split path into components
        parts = path.split("/")

        # Check for path traversal attempts
        for part in parts:
            if part == "..":
                msg = f"Path traversal detected in URI: {uri!r}"
                raise SecurityError(msg)

        # First part is the skill name
        skill_name = _validate_skill_name(parts[0])

        # Remaining parts form the reference path (if any)
        reference_path = "/".join(parts[1:]) if len(parts) > 1 else None
        if reference_path == "":
            reference_path = None

        # Validate reference path components if present
        if reference_path is not None:
            ref_parts = reference_path.split("/")
            for part in ref_parts:
                if part == "..":
                    msg = f"Path traversal detected in reference path: {uri!r}"
                    raise SecurityError(msg)

        return cls(provider=provider, skill_name=skill_name, reference_path=reference_path)


def _is_valid_provider_name(name: str) -> bool:
    """Check if a provider name is valid.

    Provider names must:
    - Be alphanumeric, hyphens, or underscores only
    - Be at most 63 characters (DNS label compatible)
    - Not be empty

    Args:
        name: Provider name to validate

    Returns:
        True if valid, False otherwise

    Examples:
        >>> _is_valid_provider_name("local")
        True
        >>> _is_valid_provider_name("my-provider_1")
        True
        >>> _is_valid_provider_name("invalid.name")
        False
        >>> _is_valid_provider_name("a" * 64)
        False
    """
    if not name:
        return False
    if len(name) > MAX_PROVIDER_NAME_LENGTH:
        return False
    return bool(PROVIDER_NAME_PATTERN.match(name))


def _validate_provider_name(name: str) -> str:
    """Validate and normalize a provider name.

    Args:
        name: Provider name to validate

    Returns:
        Normalized provider name

    Raises:
        SecurityError: If provider name is invalid
    """
    if not _is_valid_provider_name(name):
        msg = (
            f"Invalid provider name: {name!r}. "
            f"Provider names must be alphanumeric, hyphen, or underscore only, "
            f"and at most {MAX_PROVIDER_NAME_LENGTH} characters."
        )
        raise SecurityError(msg)
    return name


def _validate_skill_name(name: str) -> str:
    """Validate a skill name following Skill model rules.

    Skill names must:
    - Be lowercase
    - Be alphanumeric with hyphens only
    - Not start or end with hyphen
    - Not contain consecutive hyphens
    - Be non-empty

    Args:
        name: Skill name to validate

    Returns:
        Normalized skill name

    Raises:
        SecurityError: If skill name is invalid
    """
    # Normalize unicode
    normalized = unicodedata.normalize("NFKC", name.strip())

    if not normalized:
        msg = "Skill name must be non-empty"
        raise SecurityError(msg)

    if normalized != normalized.lower():
        msg = f"Skill name {normalized!r} must be lowercase"
        raise SecurityError(msg)

    if normalized.startswith("-") or normalized.endswith("-"):
        msg = "Skill name cannot start or end with a hyphen"
        raise SecurityError(msg)

    if "--" in normalized:
        msg = "Skill name cannot contain consecutive hyphens"
        raise SecurityError(msg)

    if not all(c.isalnum() or c in "-_" for c in normalized):
        msg = (
            f"Skill name {normalized!r} contains invalid characters. "
            "Only lowercase letters, digits, hyphens, and underscores are allowed."
        )
        raise SecurityError(msg)

    return normalized


class SkillURIResolver:
    """Resolver for skill:// URIs using registered providers.

    Manages a registry of resource providers and resolves skill URIs
    to actual skill instances.

    Example:
        >>> resolver = SkillURIResolver()
        >>> resolver.register_provider("local", local_provider)
        >>> skill = await resolver.resolve("skill://local/python-expert")
    """

    def __init__(self) -> None:
        """Initialize the resolver with an empty provider registry."""
        self._providers: dict[str, ResourceProvider] = {}

    def register_provider(self, name: str, provider: ResourceProvider) -> None:
        """Register a resource provider.

        Args:
            name: Provider name (must be valid per _is_valid_provider_name)
            provider: The resource provider instance

        Raises:
            SecurityError: If provider name is invalid
        """
        validated_name = _validate_provider_name(name)
        self._providers[validated_name] = provider

    def unregister_provider(self, name: str) -> None:
        """Unregister a resource provider.

        Args:
            name: Provider name to unregister
        """
        validated_name = _validate_provider_name(name)
        self._providers.pop(validated_name, None)

    async def resolve(self, uri: str) -> Skill:
        """Resolve a skill URI to a Skill instance.

        Args:
            uri: The skill URI to resolve

        Returns:
            The resolved Skill instance

        Raises:
            SecurityError: If URI validation fails
            SkillNotFoundError: If skill not found in provider
            ValueError: If provider not registered for explicit URIs
        """
        resolved = ResolvedSkillURI.parse(uri)

        # If no provider specified, we need to search all providers
        if resolved.provider is None:
            for provider in self._providers.values():
                skills = await provider.get_skills()
                for skill in skills:
                    if skill.name == resolved.skill_name:
                        return skill
            msg = f"Skill {resolved.skill_name!r} not found in any provider"
            raise SkillNotFoundError(msg)

        # Look up specific provider
        if resolved.provider not in self._providers:
            msg = f"Provider {resolved.provider!r} not registered"
            raise ValueError(msg)

        provider = self._providers[resolved.provider]
        skills = await provider.get_skills()

        for skill in skills:
            if skill.name == resolved.skill_name:
                return skill

        msg = f"Skill {resolved.skill_name!r} not found in provider {resolved.provider!r}"
        raise SkillNotFoundError(msg)

    def get_provider(self, name: str) -> ResourceProvider | None:
        """Get a registered provider by name.

        Args:
            name: Provider name

        Returns:
            The provider instance or None if not found
        """
        return self._providers.get(name)

    def list_providers(self) -> list[str]:
        """List all registered provider names.

        Returns:
            List of provider names
        """
        return list(self._providers.keys())
