"""Span helpers for safe instrumentation in async generators.

When ``with logfire.span(...)`` is used inside an async generator that
contains ``yield``, OpenTelemetry's context management can raise
``ValueError: Token was created in a different Context`` during
``GeneratorExit``. This happens because the span's ``__exit__`` calls
``context.detach(token)`` which tries to ``ContextVar.reset(token)``,
but the token was created in a different ``contextvars.Context`` than
the one active when the generator is closed.

OpenTelemetry catches this ``ValueError`` internally and logs it as
``"Failed to detach context"`` (WARNING level). This module suppresses
that specific log message to keep logs clean, since the error is
non-fatal — the span is still recorded, only context restoration fails.
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
import logging
from typing import TYPE_CHECKING, Any

import logfire


if TYPE_CHECKING:
    from collections.abc import Iterator


class _DetachContextFilter(logging.Filter):
    """Suppress 'Failed to detach context' warnings from OpenTelemetry.

    These warnings occur when async generators are closed via
    GeneratorExit and the OTel context token was created in a different
    contextvars.Context. The error is non-fatal — spans are still
    recorded correctly.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "Failed to detach context" not in record.getMessage()


# Install the filter on the opentelemetry.context logger.
_otel_context_logger = logging.getLogger("opentelemetry.context")
_otel_context_logger.addFilter(_DetachContextFilter())


@contextmanager
def safe_span(name: str, **attributes: Any) -> Iterator[logfire.LogfireSpan]:
    """Create a logfire span safe for use in async generators.

    Use this instead of ``with logfire.span(...)`` inside async
    generators that contain ``yield`` statements. The span's
    ``__exit__`` may trigger ``ValueError`` from OpenTelemetry's
    ``context.detach()`` when the generator is closed via
    ``GeneratorExit`` — this wrapper catches and suppresses that error,
    and the module-level filter suppresses the OTel log warning.

    Args:
        name: Span name (e.g., ``"turn.native"``).
        **attributes: Span attributes (e.g., ``session_id=...``).

    Yields:
        The logfire span object.
    """
    span = logfire.span(name, **attributes)
    span.__enter__()
    try:
        yield span
    finally:
        with suppress(ValueError):
            # Suppress "Token was created in a different Context" errors
            # that occur when async generators are closed via
            # GeneratorExit across context boundaries.
            span.__exit__(None, None, None)
