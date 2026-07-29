"""Stub dataclasses for HostContext future expansion.

CapabilityCache provides async-safe tokonomics-backed model capability
caching with single-flight deduplication. ModelRegistry and ModelCache
remain placeholders for future waves.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import importlib.util
from typing import TYPE_CHECKING

import structlog


if TYPE_CHECKING:
    from agentpool_config.model_capabilities import ModelCapabilities

logger = structlog.get_logger(__name__)

# Per-modality default values when tokonomics has no data.
_MODALITY_DEFAULTS: dict[str, bool] = {
    "image_input": True,
    "audio_input": True,
    "video_input": False,
    "document_input": False,
    "image_output": False,
}

# Modalities that have no tokonomics equivalent — always return None
# from get_capability so resolve_capabilities applies the default.
_NO_TOKONOMICS_MODALITIES: frozenset[str] = frozenset({"image_output"})

# All modality field names in ModelCapabilities.
_MODALITY_FIELDS: tuple[str, ...] = (
    "image_input",
    "audio_input",
    "video_input",
    "document_input",
    "image_output",
)


def _cache_key(model_name: str, modality: str) -> str:
    """Build a cache key from model name and modality."""
    return f"{model_name.lower()}:{modality}"


@dataclass
class CapabilityCache:
    """Async-safe cache for model capability lookups via tokonomics.

    Caches capability results by (model_name, modality) with single-flight
    deduplication: concurrent requests for the same key await the same
    asyncio.Task, avoiding redundant tokonomics queries.
    """

    _cache: dict[str, bool | None] = field(default_factory=dict)
    _inflight: dict[str, asyncio.Task[bool | None]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def get_capability(self, model_name: str, modality: str) -> bool | None:
        """Check if a model supports a given modality.

        Args:
            model_name: Name of the model to check (e.g. "openai:gpt-4o").
            modality: Modality field name (e.g. "image_input").

        Returns:
            True or False if tokonomics has data for this model+modality,
            None if tokonomics has no data or is unavailable.
        """
        key = _cache_key(model_name, modality)

        # Fast path: cached result (no lock needed — dict reads are atomic).
        if key in self._cache:
            return self._cache[key]

        async with self._lock:
            # Double-check after acquiring lock.
            if key in self._cache:
                return self._cache[key]

            # Reuse in-flight task or create a new one.
            if key in self._inflight:
                task = self._inflight[key]
            else:
                task = asyncio.create_task(
                    self._query_and_cache(model_name, modality, key),
                )
                self._inflight[key] = task

        # Await outside the lock so other callers can proceed.
        try:
            return await task
        finally:
            async with self._lock:
                self._inflight.pop(key, None)

    async def _query_and_cache(
        self,
        model_name: str,
        modality: str,
        key: str,
    ) -> bool | None:
        """Query tokonomics for a single (model_name, modality) pair and cache."""
        result = await self._query_tokonomics(model_name, modality)
        self._cache[key] = result
        return result

    async def _query_tokonomics(self, model_name: str, modality: str) -> bool | None:
        """Query tokonomics for model capability data.

        Field mapping:
            - image_input  ← supports_vision (from get_model_capabilities)
            - audio_input  ← supports_audio_input (from get_model_capabilities)
            - video_input  ← "video" in input_modalities (from ModelInfo)
            - document_input ← "file" in input_modalities (from ModelInfo)
            - image_output ← no tokonomics equivalent (always None)
        """
        if modality in _NO_TOKONOMICS_MODALITIES:
            return None

        if not importlib.util.find_spec("tokonomics"):
            logger.warning(
                "tokonomics_not_available",
                model=model_name,
                modality=modality,
            )
            return None

        try:
            match modality:
                case "image_input" | "audio_input":
                    return await self._resolve_capability_field(model_name, modality)
                case "video_input" | "document_input":
                    return await self._resolve_modality_field(model_name, modality)
                case _:
                    return None
        except Exception:
            logger.exception(
                "tokonomics_query_failed",
                model=model_name,
                modality=modality,
            )
            return None

    async def _resolve_capability_field(
        self,
        model_name: str,
        modality: str,
    ) -> bool | None:
        """Resolve image_input or audio_input from get_model_capabilities."""
        from tokonomics import get_model_capabilities

        caps = await get_model_capabilities(model_name)
        if caps is None:
            return None
        if modality == "image_input":
            return caps.supports_vision
        return caps.supports_audio_input

    async def _resolve_modality_field(
        self,
        model_name: str,
        modality: str,
    ) -> bool | None:
        """Resolve video_input or document_input from ModelInfo.input_modalities."""
        model_info = await self._get_model_info(model_name)
        if model_info is None:
            return None
        input_modalities = model_info.input_modalities  # type: ignore[attr-defined]
        target = "video" if modality == "video_input" else "file"
        return target in input_modalities

    async def _get_model_info(self, model_name: str) -> object | None:
        """Get ModelInfo for a specific model from tokonomics.

        Searches get_all_models() results by matching model_name against
        the model's id or pydantic_ai_id (case-insensitive).
        """
        try:
            from tokonomics.model_discovery import get_all_models
        except ImportError:
            return None

        try:
            all_models = await get_all_models()
        except Exception:
            logger.exception("model_info_fetch_failed", model=model_name)
            return None

        normalized = model_name.lower()
        for model in all_models:
            if normalized in (model.id.lower(), model.pydantic_ai_id.lower()):
                return model
        return None


# Module-level default cache instance.
_default_cache: CapabilityCache | None = None


def _get_default_cache() -> CapabilityCache:
    """Get or create the module-level default CapabilityCache."""
    global _default_cache  # noqa: PLW0603
    if _default_cache is None:
        _default_cache = CapabilityCache()
    return _default_cache


async def resolve_capabilities(
    model_name: str,
    declared: ModelCapabilities,
    cache: CapabilityCache | None = None,
) -> ModelCapabilities:
    """Fill None fields in declared capabilities from tokonomics.

    For each field in declared that is None, queries CapabilityCache for
    the model's capability. When tokonomics has no data, applies a
    per-modality default and logs a warning.

    Explicit (non-None) values in declared are preserved without querying
    tokonomics.

    Args:
        model_name: Name of the model to look up.
        declared: ModelCapabilities with possibly-None fields.
        cache: Optional CapabilityCache instance (uses default if omitted).

    Returns:
        New ModelCapabilities with all fields resolved to bool.
    """
    if cache is None:
        cache = _get_default_cache()

    data = declared.model_dump()
    updates: dict[str, bool] = {}

    for field_name in _MODALITY_FIELDS:
        if data[field_name] is not None:
            continue

        result = await cache.get_capability(model_name, field_name)
        if result is None:
            result = _MODALITY_DEFAULTS[field_name]
            logger.warning(
                "capability_fallback_default",
                model=model_name,
                modality=field_name,
                default=result,
            )
        updates[field_name] = result

    if not updates:
        return declared

    return declared.model_copy(update=updates)


@dataclass
class ModelRegistry:
    """Placeholder for future model provider registry."""


@dataclass
class ModelCache:
    """Placeholder for future model instance caching."""
