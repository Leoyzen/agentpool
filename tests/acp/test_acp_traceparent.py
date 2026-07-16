"""Tests for W3C trace context propagation via ACP ``_meta`` (``field_meta``).

Verifies that ``TraceContextTextMapPropagator.inject()`` produces a valid
W3C ``traceparent`` when a span is active, and that the format survives
roundtrip through extract.
"""

from __future__ import annotations

import re

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import NonRecordingSpan, SpanContext
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
import pytest


@pytest.fixture(autouse=True)
def _setup_tracer_provider():
    """Set up a global TracerProvider for the test.

    OpenTelemetry needs a provider installed for ``start_as_current_span``
    to create real (non-noop) spans.  Without it ``inject()`` produces an
    empty carrier.
    """
    trace.set_tracer_provider(TracerProvider())
    yield
    # Reset — None is valid at runtime (means "no provider")
    trace.set_tracer_provider(None)  # type: ignore[arg-type]


_TRACEPARENT_RE = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


@pytest.mark.unit
def test_traceparent_injection_format():
    """Injecting into an empty carrier produces a valid W3C traceparent.

    Given: An active OpenTelemetry span.
    When:  ``TraceContextTextMapPropagator.inject()`` runs.
    Then:  The carrier contains ``traceparent`` in the format
           ``00-<32-hex-trace-id>-<16-hex-span-id>-<2-hex-flags>``.
    """
    tracer = trace.get_tracer(__name__)
    carrier: dict[str, str] = {}

    with tracer.start_as_current_span("test-span"):
        TraceContextTextMapPropagator().inject(carrier)

    tp = carrier.get("traceparent")
    assert tp is not None, "Expected traceparent in carrier, got None"
    assert _TRACEPARENT_RE.match(tp), (
        f"traceparent '{tp}' does not match expected W3C format "
        r"'00-<32hex>-<16hex>-<2hex>'"
    )


@pytest.mark.unit
def test_traceparent_roundtrip_extract():
    """A traceparent injected from an active span is extractable.

    Given: A carrier with a traceparent from an active span.
    When:  ``TraceContextTextMapPropagator.extract()`` runs.
    Then:  The resulting context yields a valid span with non-default trace ID.
    """
    tracer = trace.get_tracer(__name__)
    carrier: dict[str, str] = {}

    with tracer.start_as_current_span("source-span"):
        TraceContextTextMapPropagator().inject(carrier)

    ctx = TraceContextTextMapPropagator().extract(carrier)
    span = trace.get_current_span(ctx)
    # A properly extracted context should carry a span with a non-zero trace ID
    assert isinstance(span, NonRecordingSpan)
    sc: SpanContext = span.get_span_context()
    assert sc.trace_id != 0, "Extracted span should have a non-zero trace ID"
    assert sc.span_id != 0, "Extracted span should have a non-zero span ID"


@pytest.mark.unit
def test_traceparent_no_active_span_skips_injection():
    """Without an active span, inject() does not populate the carrier.

    Given: No active OpenTelemetry span.
    When:  ``TraceContextTextMapPropagator.inject()`` runs.
    Then:  The carrier is empty (no traceparent).
    """
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    assert "traceparent" not in carrier, "traceparent should not be injected without an active span"
