# Documentation Guide

This guide tells you where to put new documentation in the AgentPool project.

## Where to Put New Documentation

| Content Type | Location | Example |
|---|---|---|
| New feature usage guide | `docs/how-to/` | "How to configure MCP servers" |
| Learning tutorial | `docs/tutorials/` | "Getting started with AgentPool" |
| Architecture explanation | `docs/explanation/` | "How the graph architecture works" |
| Architectural decision record | `docs/adr/` | "Why we chose SQLite WAL" — use `docs/adr/TEMPLATE.md` |
| API / config reference | `docs/reference/` | CLI commands, config schema, ACP meta fields |
| Root cause analysis | `docs/records/rca/` | "Why EventBus deadlocked on 2024-01-15" |
| Audit report | `docs/records/audit/` | "ACP protocol security audit" |
| Requirements analysis | `docs/records/requirements/` | "ACP elicitation protocol requirements" |
| Bug report | `docs/records/bugs/` | "BUG-001: run_stream breaks on error" |
| Major architectural proposal | `docs/rfcs/draft/` | RFC — see `docs/rfcs/STATUS.md` for format |
| Implementation change | `openspec/changes/` | OpenSpec change — use `/opsx:propose` |

## RFC and OpenSpec Pipeline

```
RFC (decision phase) → accepted → OpenSpec change (execution phase) → archive → RFC marked "implemented"
```

- **Major architectural decisions**: Write an RFC first. When accepted, create an OpenSpec change to implement.
- **Small changes** (bug fixes, minor features): Go directly to OpenSpec without an RFC.

## Rules

1. **Do not create a new top-level directory under `docs/`**. If your documentation doesn't fit any category above, open an issue and ask.
2. **Do not duplicate content**. If the same information exists in AGENTS.md and docs/, the docs/ version is the source of truth. AGENTS.md is a thin entry point.
3. **Use the ADR template** for architectural decisions. Copy `docs/adr/TEMPLATE.md`.
4. **Update `docs/rfcs/STATUS.md`** when an RFC changes status (draft → accepted → implemented).

## For AI-Assisted Contributors

Your AI tool reads `AGENTS.md` which contains a Context Loading table pointing to this guide. When creating documentation, follow the table above. If unsure, ask the human to open an issue.
