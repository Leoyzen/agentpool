# Specs

This change is a bugfix that restores real-time streaming for standalone agent execution. No new capabilities are introduced, and no existing capability requirements are modified.

The implementation modifies `NativeAgent._stream_events()` to detect execution context (standalone vs graph-wrapped) and use the appropriate streaming path. See `design.md` for technical details.
