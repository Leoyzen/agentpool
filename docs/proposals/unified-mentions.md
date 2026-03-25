# Proposal: Unified Mention System

## Problem

Context references (files, selections, URLs, agent delegations) are represented differently across protocols:

- **Zed**: `MentionUri` variants (`File`, `Selection`, `Symbol`, `Fetch`, etc.)
- **OpenCode**: `PartInput` variants (`TextPartInput`, `FilePartInput`, `AgentPartInput`, `SubtaskPartInput`)
- **ACP**: `ContentBlock` variants (`TextContentBlock`, `ResourceContentBlock`, `EmbeddedResourceContentBlock`)
- **Internal**: `PathReference`, raw strings, `UserContent`

Each protocol adapter converts its own mention types to flat text or `UserContent` before passing to agents. This loses structure and prevents smart context management.

## Proposal

Define a protocol-agnostic `Mention` type system in `agentpool.messaging.mentions` that all protocols map to and from. Agents receive structured mentions, and a resolution layer expands them to `UserContent` right before the LLM call.

## Proposed Types

```python
@dataclass(frozen=True)
class FileMention:
    """Reference to a file."""
    path: str
    fs: AsyncFileSystem | None = None
    mime_type: str | None = None
    display_name: str | None = None

@dataclass(frozen=True)
class DirectoryMention:
    """Reference to a directory (may be expanded to file listing or tree)."""
    path: str
    fs: AsyncFileSystem | None = None

@dataclass(frozen=True)
class SelectionMention:
    """Reference to a specific range within a file."""
    path: str
    start_line: int
    end_line: int
    fs: AsyncFileSystem | None = None

@dataclass(frozen=True)
class SymbolMention:
    """Reference to a named symbol (function, class) in a file."""
    path: str
    name: str
    start_line: int
    end_line: int

@dataclass(frozen=True)
class UrlMention:
    """Reference to a URL to be fetched."""
    url: str

@dataclass(frozen=True)
class ImageMention:
    """Inline image (already resolved)."""
    data: str  # base64
    mime_type: str | None = None

@dataclass(frozen=True)
class ResourceMention:
    """Reference to an MCP resource (resolved via tool manager)."""
    uri: str
    server_name: str | None = None

@dataclass(frozen=True)
class AgentMention:
    """Delegation to another agent."""
    agent_name: str

@dataclass(frozen=True)
class SubtaskMention:
    """Structured sub-task request."""
    agent_name: str
    prompt: str
    description: str | None = None

@dataclass(frozen=True)
class DiagnosticsMention:
    """IDE diagnostics (errors/warnings)."""
    include_errors: bool = True
    include_warnings: bool = False

@dataclass(frozen=True)
class GitDiffMention:
    """Git diff against a ref."""
    base_ref: str = "main"

@dataclass(frozen=True)
class TerminalMention:
    """Terminal output selection."""
    line_count: int

Mention = (
    FileMention | DirectoryMention | SelectionMention | SymbolMention
    | UrlMention | ImageMention | ResourceMention
    | AgentMention | SubtaskMention
    | DiagnosticsMention | GitDiffMention | TerminalMention
)
```

## Resolution Pipeline

```
Protocol Input → Mention[] → resolve(mentions) → UserContent[] → LLM
```

A `MentionResolver` converts mentions to `UserContent` right before the LLM call:

- `FileMention` → read file → `str` (or `BinaryContent` for non-text)
- `DirectoryMention` → list directory → `str`
- `SelectionMention` → read lines → `str`
- `SymbolMention` → read lines → `str`
- `UrlMention` → fetch → `str` or `BinaryContent`
- `ImageMention` → `ImageUrl`
- `ResourceMention` → resolve via MCP → `str` or `BinaryContent`
- `AgentMention` → synthetic instruction text (current behavior)
- `SubtaskMention` → synthetic instruction text (current behavior)
- `DiagnosticsMention` / `GitDiffMention` / `TerminalMention` → resolve from IDE context

## Protocol Mapping

| Mention Type         | Zed MentionUri       | OpenCode PartInput   | ACP ContentBlock       |
|---------------------|---------------------|---------------------|----------------------|
| `FileMention`       | `File`              | `FilePartInput`     | `ResourceContentBlock` |
| `DirectoryMention`  | `Directory`         | `FilePartInput`     | `ResourceContentBlock` |
| `SelectionMention`  | `Selection`         | —                   | `ResourceContentBlock` |
| `SymbolMention`     | `Symbol`            | —                   | `ResourceContentBlock` |
| `UrlMention`        | `Fetch`             | `FilePartInput`     | `ResourceContentBlock` |
| `ImageMention`      | `PastedImage`/Image | `FilePartInput`     | `ImageContentBlock`    |
| `ResourceMention`   | —                   | `FilePartInput`+src | `EmbeddedResource`     |
| `AgentMention`      | —                   | `AgentPartInput`    | —                      |
| `SubtaskMention`    | —                   | `SubtaskPartInput`  | —                      |
| `DiagnosticsMention`| `Diagnostics`       | —                   | —                      |
| `GitDiffMention`    | `GitDiff`           | —                   | —                      |
| `TerminalMention`   | `TerminalSelection` | —                   | —                      |

## What This Enables

1. **Smart context management** — knows what each context piece *is*, can prioritize/truncate intelligently
2. **Lazy resolution** — don't read files until needed, cache across turns
3. **Cross-protocol bridging** — a Zed file mention can be forwarded to an OpenCode agent without losing structure
4. **Multi-agent handoff** — structured context travels between agents without re-parsing
5. **Subsumes `PathReference`** — `FileMention`/`DirectoryMention` replace the current `PathReference` with richer semantics

## Storage & Roundtrip Persistence

Mentions should survive storage roundtrips. Rather than a separate `mentions` field on `ChatMessage`, the approach is to extend pydantic-ai's content type system:

```python
# Extend UserContent with mention types
AgentPoolContent = UserContent | Mention

# UserPromptPart.content becomes list[AgentPoolContent]
# Mentions live inline with text/images, preserving order
```

Storage serializes the full content list including unresolved mentions. On LLM call, a resolution boundary converts mentions to `UserContent`:

```
Storage → list[AgentPoolContent] → resolve() → list[UserContent] → pydantic-ai
```

This keeps mentions as first-class content in the conversation history without forking the message model. The resolution step is the only place that needs to know how to expand mentions.

## Migration Path

1. Define mention types in `agentpool.messaging.mentions`
2. Add `MentionResolver` that converts to `UserContent[]`
3. Update `extract_user_prompt_from_parts` to emit `Mention[]` instead of resolving inline
4. Update Zed/ACP converters to emit `Mention[]`
5. Deprecate `PathReference` in favor of `FileMention`/`DirectoryMention`
