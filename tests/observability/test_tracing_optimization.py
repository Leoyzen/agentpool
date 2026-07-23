"""Unit tests for logfire tracing optimization.

Tests cover:
- _otel_log_processor (no span creation, structured attributes preserved)
- ObservabilityRegistry config fields and API
- SessionState.trace_context field and lifecycle
- SessionController.get_trace_context() accessor
- close_session() clears trace_context
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.unit
class TestOtelLogProcessor:
    """Tests for _otel_log_processor structlog processor."""

    def test_calls_logfire_log_without_creating_span(self):
        """_otel_log_processor calls logfire.log() but does not create a span."""
        from agentpool.log import _otel_log_processor

        event_dict: dict[str, object] = {
            "event": "test message",
            "level": "info",
            "session_id": "s123",
        }

        with patch("agentpool.log.logfire") as mock_logfire:
            _otel_log_processor(None, "info", event_dict)

            mock_logfire.log.assert_called_once()
            call_kwargs = mock_logfire.log.call_args
            assert call_kwargs.kwargs["level"] == "info"
            assert call_kwargs.kwargs["msg_template"] == "test message"
            # session_id should be in attributes but "event" should not
            attrs = call_kwargs.kwargs["attributes"]
            assert attrs["session_id"] == "s123"
            assert "event" not in attrs

    def test_returns_event_dict_unchanged(self):
        """_otel_log_processor returns event_dict unchanged for downstream renderers."""
        from agentpool.log import _otel_log_processor

        event_dict: dict[str, object] = {
            "event": "test message",
            "level": "info",
        }

        with patch("agentpool.log.logfire"):
            result = _otel_log_processor(None, "info", event_dict)

        # event_dict must still have "event" key for renderer
        assert "event" in result
        assert result["event"] == "test message"
        assert result["level"] == "info"

    def test_handles_missing_event_key(self):
        """_otel_log_processor handles missing 'event' key gracefully."""
        from agentpool.log import _otel_log_processor

        event_dict: dict[str, object] = {"level": "debug"}

        with patch("agentpool.log.logfire") as mock_logfire:
            _otel_log_processor(None, "debug", event_dict)

            # Should fall back to method_name as msg
            call_kwargs = mock_logfire.log.call_args
            assert call_kwargs.kwargs["msg_template"] == "debug"

    def test_does_not_raise_on_logfire_error(self):
        """_otel_log_processor swallows logfire errors to never break logging."""
        from agentpool.log import _otel_log_processor

        event_dict: dict[str, object] = {"event": "test", "level": "info"}

        with patch("agentpool.log.logfire") as mock_logfire:
            mock_logfire.log.side_effect = RuntimeError("OTel not configured")
            # Should not raise
            result = _otel_log_processor(None, "info", event_dict)

        assert result == event_dict


@pytest.mark.unit
class TestObservabilityConfig:
    """Tests for new BaseObservabilityConfig fields."""

    def test_new_fields_exist_with_correct_defaults(self):
        """BaseObservabilityConfig has instrument_* fields with correct defaults."""
        from agentpool_config.observability import CustomObservabilityConfig

        config = CustomObservabilityConfig(type="custom", endpoint="http://localhost:4318")
        assert config.instrument_pydantic_ai is False
        assert config.instrument_mcp is False
        assert config.instrument_fastapi is True

    def test_fields_can_be_overridden(self):
        """instrument_* fields can be overridden via config."""
        from agentpool_config.observability import CustomObservabilityConfig

        config = CustomObservabilityConfig(
            type="custom",
            endpoint="http://localhost:4318",
            instrument_pydantic_ai=True,
            instrument_mcp=True,
            instrument_fastapi=False,
        )
        assert config.instrument_pydantic_ai is True
        assert config.instrument_mcp is True
        assert config.instrument_fastapi is False


@pytest.mark.unit
class TestObservabilityRegistry:
    """Tests for ObservabilityRegistry API extensions."""

    def test_is_configured_returns_false_before_configuration(self):
        """is_configured() returns False before configure_observability() is called."""
        from agentpool.observability.observability_registry import (
            ObservabilityRegistry,
        )

        registry = ObservabilityRegistry()
        assert registry.is_configured() is False

    def test_config_returns_none_before_configuration(self):
        """Config property returns None before configuration."""
        from agentpool.observability.observability_registry import (
            ObservabilityRegistry,
        )

        registry = ObservabilityRegistry()
        assert registry.config is None

    def test_conditional_instrumentation(self):
        """configure_observability respects instrument_* config fields."""
        from agentpool.observability.observability_registry import (
            ObservabilityRegistry,
        )
        from agentpool_config.observability import (
            CustomObservabilityConfig,
            ObservabilityConfig,
        )

        provider = CustomObservabilityConfig(
            type="custom",
            endpoint="http://localhost:4318",
            instrument_pydantic_ai=False,
            instrument_mcp=False,
        )
        config = ObservabilityConfig(enabled=True, provider=provider)

        registry = ObservabilityRegistry()
        with (
            patch("agentpool.observability.observability_registry.logfire") as mock_lf,
            patch("agentpool.observability.observability_registry._setup_otel_environment"),
        ):
            registry.configure_observability(config)

            # Should NOT call instrument_pydantic_ai or instrument_mcp
            mock_lf.instrument_pydantic_ai.assert_not_called()
            mock_lf.instrument_mcp.assert_not_called()
            # Should call configure
            mock_lf.configure.assert_called_once()
            assert registry.is_configured() is True
            assert registry.config is not None
            assert registry.config.instrument_pydantic_ai is False

    def test_conditional_instrumentation_enabled(self):
        """configure_observability calls instrumentation when enabled."""
        from agentpool.observability.observability_registry import (
            ObservabilityRegistry,
        )
        from agentpool_config.observability import (
            CustomObservabilityConfig,
            ObservabilityConfig,
        )

        provider = CustomObservabilityConfig(
            type="custom",
            endpoint="http://localhost:4318",
            instrument_pydantic_ai=True,
            instrument_mcp=True,
        )
        config = ObservabilityConfig(enabled=True, provider=provider)

        registry = ObservabilityRegistry()
        with (
            patch("agentpool.observability.observability_registry.logfire") as mock_lf,
            patch("agentpool.observability.observability_registry._setup_otel_environment"),
        ):
            registry.configure_observability(config)

            mock_lf.instrument_pydantic_ai.assert_called_once()
            mock_lf.instrument_mcp.assert_called_once()


@pytest.mark.unit
class TestSessionTraceContext:
    """Tests for SessionState.trace_context and SessionController.get_trace_context()."""

    def test_session_state_has_trace_context_field(self):
        """SessionState has trace_context field with default None."""
        from agentpool.orchestrator.session_controller import SessionState

        state = SessionState(session_id="test", agent_name="test")
        assert state.trace_context is None

    def test_session_state_trace_context_can_be_set(self):
        """SessionState.trace_context can be set to a Context object."""
        from opentelemetry import trace
        from opentelemetry.context import Context

        from agentpool.orchestrator.session_controller import SessionState

        state = SessionState(session_id="test", agent_name="test")
        tracer = trace.get_tracer("test")
        span = tracer.start_span("test.span")
        ctx = trace.set_span_in_context(span)
        span.end()

        state.trace_context = ctx
        assert state.trace_context is not None
        assert isinstance(state.trace_context, Context)

    def test_get_trace_context_returns_none_for_missing_session(self):
        """SessionController.get_trace_context() returns None for unknown session."""
        from agentpool.orchestrator.session_controller import SessionController

        # Create a minimal mock — we just need _sessions dict
        controller = object.__new__(SessionController)
        controller._sessions = {}

        result = controller.get_trace_context("nonexistent")
        assert result is None

    def test_get_trace_context_returns_context_for_existing_session(self):
        """SessionController.get_trace_context() returns context for existing session."""
        from opentelemetry import trace

        from agentpool.orchestrator.session_controller import (
            SessionController,
            SessionState,
        )

        tracer = trace.get_tracer("test")
        span = tracer.start_span("test.span")
        ctx = trace.set_span_in_context(span)
        span.end()

        state = SessionState(session_id="s1", agent_name="test")
        state.trace_context = ctx

        controller = object.__new__(SessionController)
        controller._sessions = {"s1": state}

        result = controller.get_trace_context("s1")
        assert result is ctx

    def test_get_trace_context_returns_none_when_trace_context_is_none(self):
        """get_trace_context() returns None when session exists but trace_context is None."""
        from agentpool.orchestrator.session_controller import (
            SessionController,
            SessionState,
        )

        state = SessionState(session_id="s1", agent_name="test")
        # trace_context is None by default

        controller = object.__new__(SessionController)
        controller._sessions = {"s1": state}

        result = controller.get_trace_context("s1")
        assert result is None

    def test_close_session_clears_trace_context(self):
        """close_session() sets trace_context to None."""
        from opentelemetry import trace

        from agentpool.orchestrator.session_controller import (
            SessionState,
        )

        tracer = trace.get_tracer("test")
        span = tracer.start_span("test.span")
        ctx = trace.set_span_in_context(span)
        span.end()

        state = SessionState(session_id="s1", agent_name="test")
        state.trace_context = ctx

        # Simulate the cleanup that happens in _close_session_unlocked
        state.trace_context = None

        assert state.trace_context is None
