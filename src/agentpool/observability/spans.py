"""Span helpers for safe instrumentation in async generators.

When ``with logfire.span(...)`` is used inside an async generator that
contains ``yield``, OpenTelemetry's context management can raise
``ValueError: Token was created in a different Context`` during
``GeneratorExit``. This happens because the span's ``__exit__`` calls
``context.detach(token)`` which tries to ``ContextVar.reset(token)``,
but the token was created in a different ``contextvars.Context`` than
the one active when the generator is closed.

Logfire's ``LogfireSpan.__exit__`` is decorated with
``@handle_internal_errors`` which catches this ``ValueError`` and
suppresses it — BUT this also prevents ``_end()`` from being called,
since the exception fires in ``_detach()`` before ``_end()`` is reached.
An unended span is never exported, causing "Missing Span" in SigNoz.

This module fixes the issue by calling ``_detach()`` and ``_end()``
separately, ensuring the span is always ended even if context detach
fails. It also suppresses the OTel log warning for cleanliness.
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
    generators that contain ``yield`` statements.

    The span's ``__exit__`` may trigger ``ValueError`` from
    OpenTelemetry's ``context.detach()`` when the generator is closed
    via ``GeneratorExit`` across context boundaries. Logfire's
    ``@handle_internal_errors`` decorator on ``__exit__`` catches this
    but skips ``_end()``, leaving the span unended and unexported.

    This wrapper calls ``_detach()`` and ``_end()`` separately so the
    span is always properly ended and exported, even if context
    restoration fails.

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
        # Detach context first. This may fail with ValueError when the
        # async generator is closed via GeneratorExit in a different
        # contextvars.Context than where the span was entered.
        # We suppress ALL exceptions (not just ValueError) because
        # logfire's @handle_internal_errors would also catch and log them.
        with suppress(Exception):
            span._detach()  # type: ignore[no-untyped-call]
        # End the span. This MUST always run, even if detach failed.
        # An unended span is never exported by the OTel exporter,
        # causing "Missing Span" in backends like SigNoz.
        with suppress(Exception):
            span._end()
