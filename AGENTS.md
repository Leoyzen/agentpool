# AGENTS.md — AgentPool AI Collaboration Guide

AgentPool is a unified agent orchestration framework for YAML-based configuration of heterogeneous AI agents. It bridges ACP, AG-UI, OpenCode, and MCP protocols with native PydanticAI agents.

**Core Philosophy**: Define once in YAML, expose through multiple protocols, enable seamless inter-agent collaboration.

## Development Workflow

All significant changes go through OpenSpec: `/opsx:explore` → `/opsx:propose` → `/opsx:apply` → `/opsx:archive`.

- Location: `openspec/` (24 capability specs, 34 archived changes)
- CLI: `openspec` v1.4+ — see `openspec/config.yaml`

## Quick Commands

```bash
uv sync --all-extras                    # Install
uv run pytest                           # Run tests (excludes slow/e2e/snapshot)
uv run pytest -m unit                   # Unit tests only
uv run pytest -m "e2e and not slow"     # Smoke e2e (~30s, PR-blocking)
uv run ruff check src/                  # Lint
uv run ruff format src/                 # Format
uv run --no-group docs mypy src/        # Type check
duty lint                               # All checks
agentpool run <name> "prompt"           # Run agent
agentpool serve-acp config.yml          # Start ACP server
```

## Code Style — Red Lines

- Python 3.13+ required. Use modern syntax (PEP 695 generics, `match/case`, walrus operator, `asyncio.TaskGroup`).
- PEP 8 via Ruff. Google-style docstrings (no types in Args section).
- Type hints required (`mypy --strict`). **Never** use `as any`, `@ts-ignore`, or type suppressions.
- `from __future__ import annotations` for forward references.
- Use `TYPE_CHECKING` blocks to avoid circular imports. Config models import from `agentpool_config.*`, not `agentpool.models`.
- No `getattr`/`hasattr` — provide full type safety.
- NEVER use shortcuts or leave TODOs unless explicitly asked.

## Testing

See `tests/AGENTS.md` for the complete guide. Quick rules:
- 4-layer pyramid: Unit (L1) → Integration (L2) → VCR (L3) → E2E (L4)
- `ALLOW_MODEL_REQUESTS = False` blocks real model calls by default
- New protocol handlers REQUIRE VCR tests; bug fixes REQUIRE a reproducing test
- Disable observability in tests (see `conftest.py`)

## Telemetry

Instrument critical-path code (RunLoop, Turn, delegation, protocol entry points) with `@logfire.instrument` or `with logfire.span(...)`. Never `asyncio.create_task()` without an active span. See `docs/explanation/telemetry.md` for full rules.

## Key Files

- `src/agentpool/delegation/pool.py` — AgentPool orchestration
- `src/agentpool/agents/agent.py` — Native agent implementation
- `src/agentpool/messaging/messagenode.py` — MessageNode base abstraction
- `src/agentpool/orchestrator/core.py` — EventBus, SessionController
- `src/agentpool/orchestrator/run.py` — RunHandle (RunLoop) lifecycle
- `src/agentpool/models/manifest.py` — Configuration schema
- `src/agentpool/capabilities/` — Capability system (M3)
- `src/agentpool/lifecycle/` — Lifecycle dimensions (M2)

## Context Loading

| Working on | Read this |
|---|---|
| RunLoop / Turn / EventBus | `src/agentpool/orchestrator/AGENTS.md` |
| Lifecycle dimensions | `src/agentpool/lifecycle/AGENTS.md` |
| Capabilities / tools | `src/agentpool/capabilities/AGENTS.md` |
| Skills system | `src/agentpool/skills/AGENTS.md` |
| Hooks system | `src/agentpool/hooks/AGENTS.md` |
| Tests | `tests/AGENTS.md` |
| Protocol servers | `src/agentpool_server/AGENTS.md` |
| ACP protocol | `src/acp/AGENTS.md` |
| Core framework overview | `src/agentpool/AGENTS.md` |
| Architecture deep-dives | `docs/explanation/` |
| Module structure | `docs/explanation/module-structure.md` |
| Graph architecture | `docs/explanation/graph-architecture.md` |
| Session orchestration | `docs/explanation/session-orchestration.md` |
| Lifecycle dimensions (full) | `docs/explanation/lifecycle-dimensions.md` |
| Hooks & events (full) | `docs/explanation/hooks-events.md` |
| Capabilities (full) | `docs/explanation/capabilities.md` |
| Telemetry rules | `docs/explanation/telemetry.md` |
| Usage examples | `docs/explanation/usage-examples.md` |
| Extending AgentPool | `docs/explanation/extending-agentpool.md` |
| Where to put new docs | `docs/meta/documentation-guide.md` |

## Tool Compatibility

| Tool | Config File | Status |
|------|-------------|--------|
| OpenCode | `AGENTS.md` | Native |
| Codex | `AGENTS.md` | Native |
| Claude Code | `CLAUDE.md` → `AGENTS.md` | Shim |
| Cursor | `.cursor/rules/` → `@AGENTS.md` | Shim |
| GitHub Copilot | `.github/copilot-instructions.md` → `AGENTS.md` | Shim |
| Gemini | `GEMINI.md` → `AGENTS.md` | Shim |

`AGENTS.md` is the single source of truth. All other config files are thin pointers.

## For AI-Assisted Contributors

1. Your AI tool reads this file automatically — it is the collaboration rulebook.
2. Humans should also read it (5 minutes).
3. For deeper context on a subsystem, read the sub-AGENTS.md in that directory.
4. Any significant change goes through OpenSpec: `/opsx:explore` → `/opsx:propose` → `/opsx:apply` → `/opsx:archive`.
5. Unsure where documentation goes? Open an issue, don't create a new directory.

## Rules

- ALWAYS use uv for all Python tasks.
- Maximum type safety. No `as any`, no `@ts-ignore`, no shortcuts.
- Never leave out stuff with TODOs unless explicitly asked.
