# Learnings — acp-elicitation

## 2026-05-13 Session Start
- Plan has 16 tasks across 4 waves + final verification
- Scope: ACP-only, no MCP/AG-UI/OpenCode changes
- Key constraint: preserve existing request_permission fallback exactly
- ElicitationCompleteNotification is fire-and-forget (not awaitable)
- URL-mode: elicitation/create request stays open; notification signals completion

## 2026-05-13 Final Wave + Bug Fixes
- Plan divergences from ACP RFD spec are intentional and documented
- `ElicitationCapabilities.create: bool` replaces plan's `form/url` — matches ACP spec
- `ElicitationCompleteNotification` in `ClientNotification` (client→agent) is correct direction
- NoOpClient.elicitation_create must return `action="cancel"` not `action="accept"`
- DefaultACPClient needs `elicitation_calls` tracking list for testability
- `# type: ignore[arg-type]` in notifications.py IS needed (pyright requires it even if mypy doesn't)
- Pre-existing errors in event_converter.py (TC004, PIE794) are NOT our responsibility
- Pre-existing test failures in claude_code_agent and permission_denial_sync are UNRELATED
- Subagents can create scope contamination by modifying files outside plan scope (RFC docs deleted)
- `from __future__ import annotations` makes TYPE_CHECKING imports safe for runtime annotations
