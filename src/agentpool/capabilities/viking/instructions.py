"""Workflow instructions for the Viking capability.

This module defines the instruction string injected into the agent's
system prompt when the Viking capability is active. It covers the
three-tier content model, retrieval patterns, tool selection priority,
writing strategy, URI conventions, and memory tools.

Key patterns are adapted from the OpenViking MCP server tool docstrings
and vikingbot's system prompt (context.py).
"""

from __future__ import annotations


_VIKING_INSTRUCTIONS = """\
## Viking Knowledge Graph Tools

You have access to a Viking knowledge graph via the following tools.
When acquiring information, prioritize using Viking tools to search and
read from the knowledge graph.

### Three-Tier Content Model

Viking organizes content in three tiers:

- **L0 (abstract)**: ~100 tokens — short summary stored in the graph node.
  Returned by `viking_search` and `viking_find` as snippets with scores.
- **L1 (overview)**: ~2000 tokens — medium-length overview, typically the
  first section of a document. Use `viking_read` with a small `limit`.
- **L2 (full content)**: Complete document content. Use `viking_read`
  without a limit, or `viking_grep` / `viking_glob` to find specific parts.

### Two-Step Retrieval Pattern

Always follow the two-step pattern:

1. **Search** — Use `viking_search` or `viking_find` to locate relevant
   URIs. These return L0 abstracts with relevance scores.
2. **Read** — Use `viking_read` to fetch full content (L1/L2) of the
   most relevant results.

Do NOT read full documents without searching first — searching is cheaper
and gives you relevance scores to prioritize.

A previous empty search result does not prove that a different follow-up
question has no results; search again when the query changes.

### Tool Selection — Read vs Search

| Need | Tool | When to use |
|------|------|-------------|
| Semantic search (with session) | `viking_search` | Deep retrieval with session context and intent analysis |
| Semantic search (no session) | `viking_find` | Fast semantic retrieval, deduplicates results |
| Exact text match | `viking_grep` | Regex pattern matching within documents. Use for exact text, not semantics. |
| Filename pattern match | `viking_glob` | Glob patterns over viking:// URIs (e.g. `**/*.md`). Use for finding files by name, not content. |
| Browse directory | `viking_ls` | List contents of a viking:// directory. Use `recursive=True` for deep listing. |
| Read content | `viking_read` | Fetch full or partial document content with line numbers. |

Use `viking_search`/`viking_find` for semantic retrieval (what is this about?).
Use `viking_grep` for exact text matching (where does it say "torque 450Nm"?).
Use `viking_glob` for filename discovery (which files exist under this path?).

### Writing Strategy

| Action | Tool | When to use |
|--------|------|-------------|
| Create new content | `viking_write` | New document at a specific URI |
| Edit existing content | `viking_edit` | Replace a specific string within a document |
| Ingest external files | `viking_add_resource` | Add local files or remote URLs to the Viking graph |
| Create directory | `viking_mkdir` | Organize content hierarchically |
| Delete content | `viking_forget` | Remove a document or directory (irreversible — confirm before calling) |

### URI Path Rules (Important)

Viking enforces path restrictions on write operations:

- **`viking_write`** and **`viking_edit`**: URIs must be under `memories/`
  or `resources/` paths. Other paths will be rejected by the backend.
  - Example: `viking://user/default/memories/notes/project-plan.md`
  - Example: `viking://resources/wiki/Device/SY215.md`
- **`viking_add_resource`**: The `to` parameter must target a URI under
  `viking://resources/`.
- **`viking_link`**: Both `from_uri` and all `to_uris` must point to
  existing nodes. The backend rejects links to non-existent nodes.
  Create entities before linking them.
- **`viking_forget`**: Irreversible. Confirm with the user before deleting.

Always use **full** `viking://` URIs. Never use relative paths.
Use `viking_ls` to discover available URIs when unsure.

### Memory Tools

- **`viking_remember`**: Store conversation experiences as structured
  messages. Use when the user shares preferences, important facts, or
  decisions worth persisting. Creates a session, adds messages, then
  commits for memory extraction.
- **`viking_recall`**: Retrieve stored memories by semantic query across
  multiple context types. Valid context types are:
  - `memory` — personal memories and conversation history
  - `resource` — ingested documents and resources
  - `skill` — stored skill definitions
  Results are grouped by context type with section headers.

### Graph Operations

- **`viking_link`**: Create typed links between nodes. Both source and
  target must exist. Use `reason` to label the relationship type
  (e.g., "depends-on", "references", "causes").
- **`viking_set_tags`**: Add `key=value` tags to nodes for categorization
  (e.g., `status=active`, `priority=high`). Use `recursive=True` to
  tag all children.

Use links to express relationships between entities.
Use tags for metadata and categorization.
"""
