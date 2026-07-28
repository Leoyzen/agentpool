"""Workflow instructions for the Viking capability.

This module defines the instruction string injected into the agent's
system prompt when the Viking capability is active. It covers the
three-tier content model, retrieval patterns, tool selection priority,
writing strategy, URI conventions, and memory tools.
"""

from __future__ import annotations


_VIKING_INSTRUCTIONS = """\
## Viking Knowledge Graph Tools

You have access to a Viking knowledge graph via the following tools.

### Three-Tier Content Model

Viking organizes content in three tiers:

- **L0 (abstract)**: ~100 tokens — short summary stored in the graph node.
  Returned by `viking_search` and `viking_find` as snippets.
- **L1 (overview)**: ~2000 tokens — medium-length overview, typically the
  first section of a document. Use `viking_read` with a small `limit` to get this.
- **L2 (full content)**: Complete document content. Use `viking_read` without
  a limit, or `viking_grep` / `viking_glob` to find specific parts.

### Two-Step Retrieval Pattern

Always follow the two-step pattern:

1. **Search** — Use `viking_search` or `viking_find` to locate relevant URIs.
   These return L0 abstracts (snippets) with scores.
2. **Read** — Use `viking_read` to fetch the full content (L1/L2) of the
   most relevant results.

Do NOT read full documents without searching first — searching is cheaper
and gives you relevance scores to prioritize.

### Tool Selection Priority

| Need | Tool | Notes |
|------|------|-------|
| Semantic search | `viking_search` | Uses embeddings, session-aware |
| Deduplicated search | `viking_find` | Like search but deduplicates results |
| Exact text match | `viking_grep` | Regex pattern matching within documents |
| URI pattern match | `viking_glob` | Glob patterns over viking:// URIs |
| Browse directory | `viking_ls` | List contents of a viking:// directory |
| Read content | `viking_read` | Fetch full or partial document content |

### Writing Strategy

| Action | Tool | When to use |
|--------|------|-------------|
| Create new content | `viking_write` | New document at a specific URI |
| Edit existing content | `viking_edit` | Modify a specific string within a document |
| Ingest external files | `viking_add_resource` | Add local files to the Viking graph |
| Create directory | `viking_mkdir` | Organize content hierarchically |
| Delete content | `viking_forget` | Remove a document or directory |

Use `mode="create"` (default) for `viking_write` to avoid accidentally
overwriting existing content. Only use `mode="replace"` when you intend
to overwrite.

### URI Conventions

- Always use **full** `viking://` URIs. Never use relative paths.
- Examples: `viking://user/alice/notes/project-plan.md`,
  `viking://team/engineering/specs/api-v2.md`
- Use `viking_ls` to discover available URIs when unsure.

### Memory Tools

- **`viking_remember`**: Store conversation experiences as structured
  messages. Use this to persist important context for future sessions.
- **`viking_recall`**: Retrieve stored memories by semantic query.
  Results are filtered by context type (events, entities, preferences,
  experiences) with configurable quotas.

### Graph Operations

- **`viking_link`**: Create typed links between nodes in the knowledge graph.
- **`viking_set_tags`**: Add key=value tags to nodes for categorization.

Use links to express relationships (e.g., "depends-on", "references").
Use tags for metadata (e.g., `status=active`, `priority=high`).
"""
