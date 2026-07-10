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

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable
import unicodedata
from urllib.parse import unquote, urlparse

from agentpool.skills.exceptions import SecurityError, SkillNotFoundError


if TYPE_CHECKING:
    from agentpool.skills.skill import Skill


@runtime_checkable
class SkillProvider(Protocol):
    """Protocol for capabilities that provide skills."""

    async def get_skills(self) -> list[Skill]:
        """Return the list of skills provided by this capability."""
        ...


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
        ResolvedSkillURI(provider='local', skill_name='python-expert',
        reference_path='references/guide.md')

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

        # Check if it's a bare skill name (no scheme)
        # Note: We check the original URI before decoding to avoid misinterpreting
        # URL-encoded characters as scheme separators
        if "://" not in uri:
            # URL decode the bare skill name for validation
            decoded_name = unquote(uri)
            if "\x00" in decoded_name:
                msg = "URI contains null bytes after decoding"
                raise SecurityError(msg)
            skill_name = _validate_skill_name(decoded_name)
            return cls(provider=None, skill_name=skill_name, reference_path=None)

        # Parse the URI first (before decoding) to correctly extract components
        parsed = urlparse(uri)

        # Validate scheme
        if parsed.scheme != "skill":
            msg = f"Invalid URI scheme: {parsed.scheme!r}, expected 'skill'"
            raise ValueError(msg)

        # Extract and decode provider from netloc
        provider = parsed.netloc if parsed.netloc else None
        if provider is not None:
            provider = _validate_provider_name(unquote(provider))

        # Parse and decode the path component
        path = unquote(parsed.path)
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
        # Reject absolute paths (starting with /) for defense-in-depth
        if reference_path is not None and (
            ".." in reference_path.split("/") or reference_path.startswith("/")
        ):
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
    """Validate a skill name following Agent Skills Spec.

    Skill names must:
    - Be lowercase
    - Be alphanumeric with hyphens only (kebab-case)
    - Not start or end with hyphen
    - Not contain consecutive hyphens
    - Be non-empty

    Underscores are automatically normalized to hyphens per spec.

    Args:
        name: Skill name to validate

    Returns:
        Normalized skill name (underscores replaced with hyphens)

    Raises:
        SecurityError: If skill name is invalid
    """
    # Normalize unicode
    normalized = unicodedata.normalize("NFKC", name.strip())

    # Check for null bytes BEFORE normalization (security-sensitive check)
    if "\x00" in normalized:
        msg = "Skill name contains null bytes"
        raise SecurityError(msg)

    # Normalize underscores to hyphens per Agent Skills Spec (kebab-case).
    # The spec mandates "lowercase letters, numbers, and hyphens only".
    normalized = normalized.replace("_", "-")

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

    if not all(c.isalnum() or c == "-" for c in normalized):
        msg = (
            f"Skill name {normalized!r} contains invalid characters. "
            "Only lowercase letters, digits, and hyphens are allowed."
        )
        raise SecurityError(msg)

    return normalized


def _name_alternatives(name: str) -> list[str]:
    """Generate alternative skill names by swapping - and _.

    MCP servers (e.g., FastMCP) use directory names as-is for skill
    identifiers, which may contain underscores. Models calling load_skill
    often use kebab-case by convention. This function generates the
    alternative form so the resolver can find the skill regardless of
    which convention the caller uses.

    Args:
        name: The original skill name

    Returns:
        List of alternative names (empty if name has no - or _)
    """
    if "_" in name:
        return [name.replace("_", "-")]
    if "-" in name:
        return [name.replace("-", "_")]
    return []


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
        self._providers: dict[str, SkillProvider] = {}

    def register_provider(self, name: str, provider: SkillProvider) -> None:
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

    async def _find_skill_in_providers(
        self, skill_name: str, ref_path: str | None = None
    ) -> Skill | None:
        """Search all providers for a skill by name.

        Args:
            skill_name: The skill name to search for.
            ref_path: Optional reference path to store on the skill if found.

        Returns:
            The matching Skill or None if not found.
        """
        for provider in self._providers.values():
            if not isinstance(provider, SkillProvider):
                continue
            skills = await provider.get_skills()
            for skill in skills:
                if skill.name == skill_name:
                    if ref_path is not None:
                        skill._resolved_reference_path = ref_path  # type: ignore[attr-defined]
                    return skill
        return None

    async def _find_skill_with_alternatives(self, skill_name: str) -> Skill | None:
        """Search all providers for a skill, trying name alternatives.

        Args:
            skill_name: The skill name to search for.

        Returns:
            The matching Skill or None if not found.
        """
        skill = await self._find_skill_in_providers(skill_name)
        if skill is not None:
            return skill
        for alt_name in _name_alternatives(skill_name):
            skill = await self._find_skill_in_providers(alt_name)
            if skill is not None:
                return skill
        return None

    async def resolve(self, uri: str) -> Skill:
        """Resolve a skill URI to a Skill instance.

        Supports both full URIs with provider and provider-less URIs:
        - skill://provider/skill-name - Full URI with explicit provider
        - skill://provider/skill-name/references/file.md - Full URI with reference
        - skill://skill-name/references/file.md - Provider-less URI (searches all providers)

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

        # If no provider specified, search all providers
        if resolved.provider is None:
            skill = await self._find_skill_with_alternatives(resolved.skill_name)
            if skill is not None:
                return skill
            msg = f"Skill {resolved.skill_name!r} not found in any provider"
            raise SkillNotFoundError(msg)

        # Look up specific provider
        if resolved.provider not in self._providers:
            # Provider not found - try fallback for provider-less URIs
            # This handles URIs like: skill://skill-name/references/file.md
            # where user omitted the provider
            potential_skill_name = resolved.provider
            potential_ref_path = (
                f"{resolved.skill_name}/{resolved.reference_path}"
                if resolved.reference_path
                else resolved.skill_name
            )

            # Search all providers for this skill
            skill = await self._find_skill_in_providers(
                potential_skill_name, ref_path=potential_ref_path
            )
            if skill is not None:
                return skill

            # Fallback: maybe the skill_name is actually the skill and reference is combined
            # This handles: skill://skill-name/subdir/file.md (no "references" prefix)
            if resolved.skill_name:
                potential_ref_path2 = (
                    f"{resolved.skill_name}/{resolved.reference_path}"
                    if resolved.reference_path
                    else resolved.skill_name
                )
                skill = await self._find_skill_in_providers(
                    potential_skill_name, ref_path=potential_ref_path2
                )
                if skill is not None:
                    return skill

            msg = f"Provider {resolved.provider!r} not registered"
            raise ValueError(msg)

        provider = self._providers[resolved.provider]
        if not isinstance(provider, SkillProvider):
            msg = f"Provider {resolved.provider!r} does not implement SkillProvider"
            raise TypeError(msg)
        skills = await provider.get_skills()

        for skill in skills:
            if skill.name == resolved.skill_name:
                return skill

        # Fuzzy match: try swapping - and _ in the skill name
        for alt_name in _name_alternatives(resolved.skill_name):
            for skill in skills:
                if skill.name == alt_name:
                    return skill

        msg = f"Skill {resolved.skill_name!r} not found in provider {resolved.provider!r}"
        raise SkillNotFoundError(msg)

    def get_provider(self, name: str) -> SkillProvider | None:
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
