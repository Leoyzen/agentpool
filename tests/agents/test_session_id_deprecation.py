"""Tests for AgentRunContext.session_id deprecation (RFC-0028 T8)."""

from __future__ import annotations

from dataclasses import asdict
import warnings

from agentpool.agents.context import AgentRunContext, _DeprecatedField


# ---------------------------------------------------------------------------
# Deprecation descriptor — warning behaviour
# ---------------------------------------------------------------------------


def test_session_id_get_emits_deprecation_warning() -> None:
    """Accessing session_id on an instance emits DeprecationWarning."""
    ctx = AgentRunContext()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _ = ctx.session_id
    assert len(caught) == 1
    assert issubclass(caught[0].category, DeprecationWarning)
    assert "deprecated" in str(caught[0].message).lower()


def test_session_id_set_emits_deprecation_warning() -> None:
    """Setting session_id on an instance emits DeprecationWarning."""
    ctx = AgentRunContext()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ctx.session_id = "custom-id"
    assert len(caught) == 1
    assert issubclass(caught[0].category, DeprecationWarning)


def test_session_id_get_returns_uuid_by_default() -> None:
    """Default session_id is a UUID hex string (backward compat)."""
    ctx = AgentRunContext()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        sid = ctx.session_id
    assert isinstance(sid, str)
    assert len(sid) == 32  # uuid4.hex is 32 chars


def test_session_id_set_and_get_roundtrip() -> None:
    """Set value persists and is returned on subsequent get."""
    ctx = AgentRunContext()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        ctx.session_id = "my-session"
        assert ctx.session_id == "my-session"


def test_session_id_independent_per_instance() -> None:
    """Each AgentRunContext instance gets its own session_id."""
    ctx_a = AgentRunContext()
    ctx_b = AgentRunContext()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert ctx_a.session_id != ctx_b.session_id


def test_class_level_access_returns_descriptor() -> None:
    """Accessing session_id on the class returns the descriptor itself."""
    desc = AgentRunContext.session_id
    assert isinstance(desc, _DeprecatedField)


# ---------------------------------------------------------------------------
# asdict() compatibility
# ---------------------------------------------------------------------------


def test_asdict_includes_session_id() -> None:
    """dataclasses.asdict() includes session_id in the result."""
    ctx = AgentRunContext()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        d = asdict(ctx)
    assert "session_id" in d
    assert isinstance(d["session_id"], str)


def test_asdict_session_id_value_matches_direct_access() -> None:
    """Value from asdict() matches direct getattr access."""
    ctx = AgentRunContext()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        ctx.session_id = "asdict-test"
        d = asdict(ctx)
    assert d["session_id"] == "asdict-test"


def test_asdict_triggers_deprecation_warning() -> None:
    """asdict() calls getattr() internally, which triggers the warning."""
    ctx = AgentRunContext()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _ = asdict(ctx)
    # asdict iterates over __dataclass_fields__ and calls getattr for each.
    # At minimum the session_id access must emit a warning.
    session_warnings = [w for w in caught if "session_id" in str(w.message)]
    assert len(session_warnings) >= 1


# ---------------------------------------------------------------------------
# Other fields unaffected
# ---------------------------------------------------------------------------


def test_other_fields_unaffected() -> None:
    """Non-deprecated fields work without warnings."""
    ctx = AgentRunContext(depth=3, deps={"key": "val"})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert ctx.depth == 3
        assert ctx.deps == {"key": "val"}
        assert ctx.cancelled is False
    # No warnings from non-deprecated fields
    assert len(caught) == 0


# ---------------------------------------------------------------------------
# session_id not in __init__ signature
# ---------------------------------------------------------------------------


def test_session_id_in_init() -> None:
    """session_id is still in __init__ for backward compatibility."""
    import inspect

    sig = inspect.signature(AgentRunContext)
    assert "session_id" in sig.parameters
    # Can construct with explicit session_id
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        ctx = AgentRunContext(session_id="explicit-id")
        assert ctx.session_id == "explicit-id"
