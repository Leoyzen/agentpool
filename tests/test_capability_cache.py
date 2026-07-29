"""Unit tests for CapabilityCache and resolve_capabilities.

Tests cover caching behavior, single-flight concurrency, tokonomics
fallback defaults, and explicit override semantics.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.host.stubs import CapabilityCache, resolve_capabilities
from agentpool_config.model_capabilities import ModelCapabilities


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_toko_capabilities(
    *,
    supports_vision: bool = False,
    supports_audio_input: bool = False,
) -> MagicMock:
    """Build a mock tokonomics ModelCapabilities object."""
    caps = MagicMock()
    caps.supports_vision = supports_vision
    caps.supports_audio_input = supports_audio_input
    return caps


def _make_model_info(
    *,
    model_id: str = "test-model",
    pydantic_ai_id: str = "test-provider:test-model",
    input_modalities: set[str] | None = None,
) -> MagicMock:
    """Build a mock tokonomics ModelInfo object."""
    info = MagicMock()
    info.id = model_id
    info.pydantic_ai_id = pydantic_ai_id
    info.input_modalities = input_modalities or {"text"}
    return info


# ---------------------------------------------------------------------------
# Task 2.4 — Caching behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_avoids_repeated_tokonomics_query() -> None:
    """Second call with same (model_name, modality) uses cached result."""
    cache = CapabilityCache()
    mock_caps = _make_toko_capabilities(supports_vision=True)

    with patch(
        "tokonomics.get_model_capabilities",
        new_callable=AsyncMock,
        return_value=mock_caps,
    ) as mock_get:
        result1 = await cache.get_capability("openai:gpt-4o", "image_input")
        result2 = await cache.get_capability("openai:gpt-4o", "image_input")

    assert result1 is True
    assert result2 is True
    assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_cache_different_keys_query_separately() -> None:
    """Different (model_name, modality) pairs are cached separately."""
    cache = CapabilityCache()
    mock_caps = _make_toko_capabilities(
        supports_vision=True,
        supports_audio_input=False,
    )

    with patch(
        "tokonomics.get_model_capabilities",
        new_callable=AsyncMock,
        return_value=mock_caps,
    ) as mock_get:
        result_vision = await cache.get_capability("openai:gpt-4o", "image_input")
        result_audio = await cache.get_capability("openai:gpt-4o", "audio_input")

    assert result_vision is True
    assert result_audio is False
    # get_model_capabilities is called once per unique (model, modality).
    assert mock_get.call_count == 2


@pytest.mark.asyncio
async def test_cache_case_insensitive_model_name() -> None:
    """Model names are normalized to lowercase for cache keys."""
    cache = CapabilityCache()
    mock_caps = _make_toko_capabilities(supports_vision=True)

    with patch(
        "tokonomics.get_model_capabilities",
        new_callable=AsyncMock,
        return_value=mock_caps,
    ) as mock_get:
        result1 = await cache.get_capability("openai:GPT-4o", "image_input")
        result2 = await cache.get_capability("openai:gpt-4o", "image_input")

    assert result1 is True
    assert result2 is True
    assert mock_get.call_count == 1


# ---------------------------------------------------------------------------
# Task 2.4 — Single-flight concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_flight_concurrent_same_key() -> None:
    """Two concurrent calls for same key await the same task."""
    cache = CapabilityCache()
    mock_caps = _make_toko_capabilities(supports_vision=True)

    call_event = asyncio.Event()
    call_count = 0

    async def slow_get_capabilities(model: str) -> MagicMock:
        nonlocal call_count
        call_count += 1
        await call_event.wait()
        return mock_caps

    with patch("tokonomics.get_model_capabilities", new=slow_get_capabilities):
        # Start both calls concurrently — they share the same in-flight task.
        task1 = asyncio.create_task(
            cache.get_capability("openai:gpt-4o", "image_input"),
        )
        task2 = asyncio.create_task(
            cache.get_capability("openai:gpt-4o", "image_input"),
        )

        # Let the event loop schedule both tasks.
        await asyncio.sleep(0)

        # Release the slow query.
        call_event.set()

        result1 = await task1
        result2 = await task2

    assert result1 is True
    assert result2 is True
    # Only one tokonomics query despite two concurrent calls.
    assert call_count == 1


@pytest.mark.asyncio
async def test_single_flight_different_keys_run_concurrently() -> None:
    """Concurrent calls for different keys create separate tasks."""
    cache = CapabilityCache()
    mock_caps = _make_toko_capabilities(
        supports_vision=True,
        supports_audio_input=False,
    )

    with patch(
        "tokonomics.get_model_capabilities",
        new_callable=AsyncMock,
        return_value=mock_caps,
    ) as mock_get:
        results = await asyncio.gather(
            cache.get_capability("openai:gpt-4o", "image_input"),
            cache.get_capability("openai:gpt-4o", "audio_input"),
        )

    assert results[0] is True
    assert results[1] is False
    assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# Task 2.4 — Tokonomics fallback with per-modality defaults
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_image_input_default_when_no_data() -> None:
    """image_input defaults to True (optimistic) when tokonomics has no data."""
    cache = CapabilityCache()
    declared = ModelCapabilities()

    with patch(
        "tokonomics.get_model_capabilities",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resolved = await resolve_capabilities("unknown:model", declared, cache=cache)

    assert resolved.image_input is True


@pytest.mark.asyncio
async def test_fallback_audio_input_default_when_no_data() -> None:
    """audio_input defaults to True (optimistic) when tokonomics has no data."""
    cache = CapabilityCache()
    declared = ModelCapabilities()

    with patch(
        "tokonomics.get_model_capabilities",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resolved = await resolve_capabilities("unknown:model", declared, cache=cache)

    assert resolved.audio_input is True


@pytest.mark.asyncio
async def test_fallback_video_input_default_when_no_data() -> None:
    """video_input defaults to False (pessimistic) when tokonomics has no data."""
    cache = CapabilityCache()
    declared = ModelCapabilities()

    with (
        patch(
            "tokonomics.get_model_capabilities",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "tokonomics.model_discovery.get_all_models",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        resolved = await resolve_capabilities("unknown:model", declared, cache=cache)

    assert resolved.video_input is False


@pytest.mark.asyncio
async def test_fallback_document_input_default_when_no_data() -> None:
    """document_input defaults to False (pessimistic) when tokonomics has no data."""
    cache = CapabilityCache()
    declared = ModelCapabilities()

    with (
        patch(
            "tokonomics.get_model_capabilities",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "tokonomics.model_discovery.get_all_models",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        resolved = await resolve_capabilities("unknown:model", declared, cache=cache)

    assert resolved.document_input is False


@pytest.mark.asyncio
async def test_fallback_image_output_always_false() -> None:
    """image_output has no tokonomics equivalent — always defaults to False."""
    cache = CapabilityCache()
    declared = ModelCapabilities()

    resolved = await resolve_capabilities("openai:gpt-4o", declared, cache=cache)

    assert resolved.image_output is False


@pytest.mark.asyncio
async def test_fallback_all_defaults_when_tokonomics_errors() -> None:
    """All modalities use defaults when tokonomics functions raise errors."""
    cache = CapabilityCache()
    declared = ModelCapabilities()

    with (
        patch(
            "tokonomics.get_model_capabilities",
            new_callable=AsyncMock,
            side_effect=ImportError("no tokonomics"),
        ),
        patch(
            "tokonomics.model_discovery.get_all_models",
            new_callable=AsyncMock,
            side_effect=ImportError("no tokonomics"),
        ),
    ):
        resolved = await resolve_capabilities("openai:gpt-4o", declared, cache=cache)

    assert resolved.image_input is True
    assert resolved.audio_input is True
    assert resolved.video_input is False
    assert resolved.document_input is False
    assert resolved.image_output is False


# ---------------------------------------------------------------------------
# Task 2.4 — Tokonomics data populates correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tokonomics_vision_resolves_image_input() -> None:
    """supports_vision=True maps to image_input=True."""
    cache = CapabilityCache()
    declared = ModelCapabilities()
    mock_caps = _make_toko_capabilities(supports_vision=True)

    with patch(
        "tokonomics.get_model_capabilities",
        new_callable=AsyncMock,
        return_value=mock_caps,
    ):
        resolved = await resolve_capabilities("openai:gpt-4o", declared, cache=cache)

    assert resolved.image_input is True


@pytest.mark.asyncio
async def test_tokonomics_audio_resolves_audio_input() -> None:
    """supports_audio_input=True maps to audio_input=True."""
    cache = CapabilityCache()
    declared = ModelCapabilities()
    mock_caps = _make_toko_capabilities(
        supports_vision=False,
        supports_audio_input=True,
    )

    with patch(
        "tokonomics.get_model_capabilities",
        new_callable=AsyncMock,
        return_value=mock_caps,
    ):
        resolved = await resolve_capabilities("openai:gpt-4o", declared, cache=cache)

    assert resolved.audio_input is True


@pytest.mark.asyncio
async def test_tokonomics_video_from_input_modalities() -> None:
    """'video' in input_modalities maps to video_input=True."""
    cache = CapabilityCache()
    declared = ModelCapabilities()
    mock_caps = _make_toko_capabilities()
    mock_model = _make_model_info(
        model_id="test-model",
        pydantic_ai_id="test-provider:test-model",
        input_modalities={"text", "image", "video"},
    )

    with (
        patch(
            "tokonomics.get_model_capabilities",
            new_callable=AsyncMock,
            return_value=mock_caps,
        ),
        patch(
            "tokonomics.model_discovery.get_all_models",
            new_callable=AsyncMock,
            return_value=[mock_model],
        ),
    ):
        resolved = await resolve_capabilities("test-provider:test-model", declared, cache=cache)

    assert resolved.video_input is True
    assert resolved.document_input is False


@pytest.mark.asyncio
async def test_tokonomics_document_from_input_modalities() -> None:
    """'file' in input_modalities maps to document_input=True."""
    cache = CapabilityCache()
    declared = ModelCapabilities()
    mock_caps = _make_toko_capabilities()
    mock_model = _make_model_info(
        model_id="test-model",
        pydantic_ai_id="test-provider:test-model",
        input_modalities={"text", "image", "file"},
    )

    with (
        patch(
            "tokonomics.get_model_capabilities",
            new_callable=AsyncMock,
            return_value=mock_caps,
        ),
        patch(
            "tokonomics.model_discovery.get_all_models",
            new_callable=AsyncMock,
            return_value=[mock_model],
        ),
    ):
        resolved = await resolve_capabilities("test-provider:test-model", declared, cache=cache)

    assert resolved.document_input is True
    assert resolved.video_input is False


# ---------------------------------------------------------------------------
# Task 2.4 — Explicit override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_override_false_preserved() -> None:
    """When declared.image_input=False, resolve returns False (explicit override)."""
    cache = CapabilityCache()
    declared = ModelCapabilities(image_input=False)
    mock_caps = _make_toko_capabilities(supports_vision=True)

    with patch(
        "tokonomics.get_model_capabilities",
        new_callable=AsyncMock,
        return_value=mock_caps,
    ):
        resolved = await resolve_capabilities("openai:gpt-4o", declared, cache=cache)

    # Explicit override preserved — tokonomics says True but declared says False.
    assert resolved.image_input is False


@pytest.mark.asyncio
async def test_explicit_override_true_preserved() -> None:
    """When declared.image_input=True, resolve returns True (explicit override)."""
    cache = CapabilityCache()
    declared = ModelCapabilities(image_input=True)
    mock_caps = _make_toko_capabilities(supports_vision=False)

    with patch(
        "tokonomics.get_model_capabilities",
        new_callable=AsyncMock,
        return_value=mock_caps,
    ):
        resolved = await resolve_capabilities("openai:gpt-4o", declared, cache=cache)

    # Explicit override preserved — tokonomics says False but declared says True.
    assert resolved.image_input is True


@pytest.mark.asyncio
async def test_mixed_override_and_resolution() -> None:
    """Explicit fields are preserved; None fields are resolved from tokonomics."""
    cache = CapabilityCache()
    declared = ModelCapabilities(
        image_input=False,
        audio_input=None,
    )
    mock_caps = _make_toko_capabilities(
        supports_vision=True,
        supports_audio_input=True,
    )

    with patch(
        "tokonomics.get_model_capabilities",
        new_callable=AsyncMock,
        return_value=mock_caps,
    ) as mock_get:
        resolved = await resolve_capabilities("openai:gpt-4o", declared, cache=cache)

    # Explicit override preserved.
    assert resolved.image_input is False
    # None field resolved from tokonomics.
    assert resolved.audio_input is True
    # Only called for audio_input (image_input was explicit, video/document
    # use get_all_models, image_output has no tokonomics equivalent).
    assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_all_explicit_no_queries() -> None:
    """When all fields are explicitly set, no tokonomics queries are made."""
    cache = CapabilityCache()
    declared = ModelCapabilities(
        image_input=True,
        audio_input=False,
        video_input=True,
        document_input=False,
        image_output=False,
    )

    with (
        patch(
            "tokonomics.get_model_capabilities",
            new_callable=AsyncMock,
        ) as mock_caps,
        patch(
            "tokonomics.model_discovery.get_all_models",
            new_callable=AsyncMock,
        ) as mock_models,
    ):
        resolved = await resolve_capabilities("openai:gpt-4o", declared, cache=cache)

    assert resolved.image_input is True
    assert resolved.audio_input is False
    assert resolved.video_input is True
    assert resolved.document_input is False
    assert resolved.image_output is False
    mock_caps.assert_not_called()
    mock_models.assert_not_called()


# ---------------------------------------------------------------------------
# Task 2.4 — Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tokonomics_exception_returns_none_and_uses_default() -> None:
    """When tokonomics raises, get_capability returns None and default is used."""
    cache = CapabilityCache()
    declared = ModelCapabilities()

    with patch(
        "tokonomics.get_model_capabilities",
        new_callable=AsyncMock,
        side_effect=RuntimeError("network error"),
    ):
        resolved = await resolve_capabilities("openai:gpt-4o", declared, cache=cache)

    # Exception caught, None returned, optimistic default applied.
    assert resolved.image_input is True


@pytest.mark.asyncio
async def test_get_capability_returns_none_for_image_output() -> None:
    """image_output has no tokonomics equivalent — get_capability returns None."""
    cache = CapabilityCache()
    result = await cache.get_capability("openai:gpt-4o", "image_output")
    assert result is None
