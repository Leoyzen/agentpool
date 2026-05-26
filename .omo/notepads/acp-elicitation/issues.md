# Issues — acp-elicitation

## Code Quality Review Findings (F2)

### P2 Issues
1. **Missing re-export** (`src/acp/schema/notifications.py` → `src/acp/schema/messages.py:15`): `ElicitationCompleteNotification` imported from `elicitation.py` and used in `ClientNotification` union but not explicitly re-exported. mypy strict mode (`no_implicit_re_export`) flags this. Fix: add `__all__` to notifications.py or import directly from elicitation in messages.py.

2. **Unused type:ignore** (`src/acp/agent/notifications.py:653`): `# type: ignore[arg-type]` on `send_elicitation_complete` is no longer needed per mypy. Should be removed.

### P3 Issues  
3. **Protocol direction question** (`src/acp/agent/notifications.py:629-654`): `send_elicitation_complete` sends `ElicitationCompleteNotification` (a ClientNotification) via `session_update` (an AgentNotification channel). Semantically backward per ACP spec. May be dead code or needs different routing.

4. **Pre-existing dead branch** (`src/acp/client/connection.py:264-268`): Two identical `case str() if method.startswith("_") and is_notification:` branches. Second never executes.

### Verdict: APPROVE
- 14/16 files clean
- No P0/P1 issues
- No AI slop, no print statements, no empty catches, no unused imports
- All public methods have docstrings
