---
rfc_id: RFC-0020
title: MCP Skills Resources Provider Protocol Support
status: DRAFT
author: AgentPool Team
reviewers: []
created: 2025-04-02
last_updated: 2025-04-02
decision_date:
related_prds: []
related_rfcs: []
---

# RFC-0020: MCP Skills Resources Provider Protocol Support

## Overview

This RFC proposes a design for AgentPool to support the MCP Skills Resources Provider protocol, enabling skills to be exposed as MCP resources while maintaining compatibility with existing skill and slash command interfaces. The implementation will allow AgentPool to act as both a consumer and provider of skills via MCP, facilitating skill discovery, sharing, and interoperability across different AI tools and agents.

The protocol enables:
1. **Skill-as-Resource**: Expose AgentPool's skills as MCP resources using the `skill://` URI scheme
2. **Bidirectional Flow**: Consume skills from external MCP servers and provide local skills to MCP clients
3. **Unified Interface**: Skills work seamlessly across skill commands, slash commands, and MCP resource access

## Table of Contents

- [Background & Context](#background--context)
- [Problem Statement](#problem-statement)
- [Goals & Non-Goals](#goals--non-goals)
- [Evaluation Criteria](#evaluation-criteria)
- [Options Analysis](#options-analysis)
- [Recommendation](#recommendation)
- [Technical Design](#technical-design)
- [Security Considerations](#security-considerations)
- [Implementation Plan](#implementation-plan)
- [Open Questions](#open-questions)
- [Decision Record](#decision-record)
- [References](#references)

---

## Background & Context

### Current State

AgentPool currently has a comprehensive skill system with the following components:

1. **SkillsRegistry** (`agentpool/skills/registry.py`): Discovers and manages skills from filesystem directories
2. **Skill Model** (`agentpool/skills/skill.py`): Pydantic model representing a skill with metadata from SKILL.md frontmatter
3. **SkillsManager** (`agentpool/skills/manager.py`): Pool-wide skill management with discovery
4. **SkillCommandRegistry** (`agentpool/skills/command_registry.py`): Exposes skills as slash commands
5. **SkillsInstructionProvider** (`agentpool/resource_providers/skills_instruction.py`): Injects skills into agent prompts

**Skill Structure**: Skills are directories containing:
- `SKILL.md`: Main instruction file with YAML frontmatter
- Supporting files: References, examples, templates

**Example skill directory**:
```
~/.claude/skills/
├── pdf-processing/
│   ├── SKILL.md           # Main instructions with frontmatter
│   ├── reference.md       # Supporting documentation
│   └── examples/
│       └── sample.pdf
└── code-review/
    └── SKILL.md
```

**MCP Integration**: AgentPool already has MCP client and server capabilities:
- **MCPManager** (`agentpool/mcp_server/manager.py`): Manages MCP server connections
- **MCPResourceProvider** (`agentpool/resource_providers/mcp_provider.py`): Wraps MCP servers as ResourceProviders
- **MCPServer** (`agentpool_server/mcp_server/server.py`): Exposes AgentPool as an MCP server

### MCP Skills Resources Provider Protocol

The [FastMCP Skills Provider](https://gofastmcp.com/servers/providers/skills) defines a standard for exposing skills as MCP resources:

**Resource URI Scheme**: `skill://{skill-name}/{file-path}`

**Resource Types**:
1. **Main file**: `skill://{skill-name}/SKILL.md` - Primary skill instructions
2. **Manifest**: `skill://{skill-name}/_manifest` - JSON listing all files with hashes
3. **Supporting files**: `skill://{skill-name}/{path}` - Any additional files

**Manifest Format**:
```json
{
  "skill": "pdf-processing",
  "files": [
    {"path": "SKILL.md", "size": 1234, "hash": "sha256:abc123..."},
    {"path": "reference.md", "size": 567, "hash": "sha256:def456..."}
  ]
}
```

### Glossary

| Term | Definition |
|------|------------|
| **Skill** | A directory with SKILL.md and supporting files that teaches an AI how to perform a task |
| **MCP** | Model Context Protocol - standard for AI tool interoperability |
| **Resource Provider** | Component that exposes resources via MCP protocol |
| **Skill URI** | `skill://` scheme URI for addressing skill files |
| **Slash Command** | User-invokable command interface (e.g., `/skill-name`) |
| **ResourceProvider** | AgentPool abstraction for providing tools/prompts/resources |

---

## Problem Statement

### The Problem

Currently, AgentPool skills are limited to local filesystem access:

1. **No Network Discovery**: Skills can only be discovered from local directories (`~/.claude/skills/`, `.claude/skills/`)
2. **No Skill Sharing**: Cannot consume skills from remote MCP servers or share local skills via MCP
3. **Fragmented Ecosystem**: Skills are siloed per tool (Claude Code, Cursor, etc.) with no interoperability
4. **Manual Distribution**: Sharing skills requires manual file copying or git submodules

### Evidence

- Users must manually sync skills across multiple machines
- No way to leverage skills published by external MCP servers
- Skill registries (e.g., Anthropic's skills repo) require manual download
- Other AI tools (Cursor, Goose) cannot access AgentPool's skills

### Impact of Inaction

| Impact | Description |
|--------|-------------|
| **Operational Cost** | Manual skill distribution across teams and environments |
| **Missed Opportunities** | Cannot leverage community skill repositories |
| **Ecosystem Fragmentation** | Skills remain tool-specific rather than universal |
| **Maintenance Burden** | Each team maintains separate skill copies |

---

## Goals & Non-Goals

### Goals (In Scope)

1. **Expose Skills as MCP Resources**: Implement `skill://` URI scheme for all skills in SkillsRegistry
2. **Consume External Skills**: Discover and use skills from external MCP servers via MCPManager
3. **Maintain Backward Compatibility**: Existing skill commands and slash commands continue to work
4. **Unified Skill Interface**: Single skill registry regardless of source (local, MCP, or hybrid)
5. **Manifest Support**: Generate and parse skill manifests for integrity verification

### Non-Goals (Out of Scope)

1. **Skill Versioning**: Version management and update mechanisms (deferred to future RFC)
2. **Skill Marketplace**: Discovery/search across skill repositories
3. **Write Operations**: Skill creation/modification via MCP (read-only access)
4. **Non-SKILL.md Skills**: Support for alternative skill formats

### Success Criteria

- [ ] Skills from `~/.claude/skills/` are accessible via `skill://` URIs
- [ ] MCP clients can list and read skill resources from AgentPool
- [ ] AgentPool can consume skills from external MCP servers
- [ ] Skill commands work identically for local and MCP-sourced skills
- [ ] Manifest generation includes file hashes for integrity

---

## Evaluation Criteria

| Criterion | Weight | Description | Minimum Threshold |
|-----------|--------|-------------|-------------------|
| **Protocol Compliance** | High | Full compatibility with FastMCP Skills Provider spec | Pass all protocol conformance tests |
| **Backward Compatibility** | High | No breaking changes to existing skill APIs | All existing tests pass |
| **Performance** | Medium | <100ms latency for skill resource access | P99 latency under threshold |
| **Security** | High | Safe handling of external skill content | Pass security review |
| **Maintainability** | Medium | Clean integration with existing architecture | <500 lines of new core code |
| **Ecosystem Compatibility** | Medium | Works with Claude Code, Cursor, Goose, etc. | Tested with 2+ external tools |

---

## Options Analysis

### Option 1: Extend SkillsRegistry with MCP Provider

**Description**

Extend the existing `SkillsRegistry` to support both filesystem and MCP sources. Add a new `MCPSkillProvider` class that implements the same interface as filesystem discovery but fetches skills from MCP servers.

**Architecture**:
```
SkillsRegistry
├── FilesystemProvider (existing)
└── MCPProvider (new)
    ├── Connects to MCP servers
    ├── Lists skill:// resources
    └── Caches skill content
```

**Advantages**

- Minimal changes to existing code
- Leverages existing SkillsRegistry lifecycle
- Natural extension of current discovery mechanism
- Skills appear transparently regardless of source

**Disadvantages**

- SkillsRegistry becomes more complex with mixed sources
- Async filesystem + MCP mixing may complicate caching
- Less clear separation of concerns

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Protocol Compliance | Good | Can fully implement spec |
| Backward Compatibility | Excellent | Minimal API changes |
| Performance | Good | Unified caching strategy |
| Security | Good | Centralized security controls |
| Maintainability | Fair | Adds complexity to registry |
| Ecosystem Compatibility | Good | Transparent to consumers |

**Effort Estimate**

- Complexity: Medium
- Resources: 1-2 developers
- Dependencies: Existing MCPManager

---

### Option 2: Separate MCPSkillResourceProvider

**Description**

Create a new `MCPSkillResourceProvider` class implementing `ResourceProvider` interface. This provider wraps MCP servers and exposes their skills as AgentPool resources, separate from SkillsRegistry.

**Architecture**:
```
AgentPool
├── SkillsRegistry (filesystem only)
└── MCPSkillResourceProvider (new)
    ├── Wraps MCPManager
    ├── Exposes skills as resources
    └── Integrates with slash commands
```

**Advantages**

- Clean separation of local vs remote skills
- Follows existing ResourceProvider pattern
- Easy to disable/enable MCP skill access
- Aligns with how other MCP resources are handled

**Disadvantages**

- Skills from different sources have different interfaces
- May need merging logic for unified skill commands
- Two skill systems to maintain

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Protocol Compliance | Good | Can fully implement spec |
| Backward Compatibility | Good | No changes to SkillsRegistry |
| Performance | Good | Separate caching per provider |
| Security | Good | Isolated security boundary |
| Maintainability | Good | Clear separation of concerns |
| Ecosystem Compatibility | Fair | Consumers need to check both sources |

**Effort Estimate**

- Complexity: Medium
- Resources: 1-2 developers
- Dependencies: Existing MCPManager

---

### Option 3: FastMCP SkillsDirectoryProvider Integration

**Description**

Use FastMCP's built-in `SkillsDirectoryProvider` to expose skills. Create an adapter between FastMCP's provider interface and AgentPool's ResourceProvider system.

**Architecture**:
```
AgentPool
├── SkillsRegistry
└── FastMCPAdapter (new)
    └── SkillsDirectoryProvider (FastMCP)
        └── Exposes skill:// resources
```

**Advantages**

- Uses battle-tested FastMCP implementation
- Less code to maintain
- Automatic protocol compliance
- Supports all FastMCP features (reload, templates, etc.)

**Disadvantages**

- Additional dependency on FastMCP internals
- Less control over behavior
- May not fit perfectly with AgentPool's architecture
- Adapter complexity

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Protocol Compliance | Excellent | Native FastMCP implementation |
| Backward Compatibility | Good | Adapter handles mapping |
| Performance | Good | FastMCP optimized implementation |
| Security | Good | FastMCP handles safety |
| Maintainability | Fair | Dependency on FastMCP internals |
| Ecosystem Compatibility | Excellent | Native protocol support |

**Effort Estimate**

- Complexity: Medium-High
- Resources: 1-2 developers
- Dependencies: FastMCP provider internals

---

### Option 4: Native MCP Resource Implementation

**Description**

Implement MCP resource handlers directly in `MCPServer` without using FastMCP's SkillsDirectoryProvider. Manually implement `list_resources`, `read_resource`, and manifest generation.

**Architecture**:
```
MCPServer
├── Existing tool/prompt handlers
└── New resource handlers (manual implementation)
    ├── list_resources() → skill:// URIs
    ├── read_resource() → skill content
    └── Manifest generation
```

**Advantages**

- Full control over implementation
- No external dependencies beyond MCP protocol
- Can optimize for AgentPool's specific needs
- Deep integration with existing server

**Disadvantages**

- More code to write and maintain
- Must ensure protocol compliance manually
- Reimplementing what FastMCP provides

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Protocol Compliance | Good | Manual implementation risk |
| Backward Compatibility | Excellent | Full control over behavior |
| Performance | Good | Can optimize specifically |
| Security | Good | Full control over validation |
| Maintainability | Fair | More custom code |
| Ecosystem Compatibility | Good | Direct protocol implementation |

**Effort Estimate**

- Complexity: High
- Resources: 2 developers
- Dependencies: MCP protocol types only

---

### Options Comparison Summary

| Criterion | Option 1 (Extend Registry) | Option 2 (Separate Provider) | Option 3 (FastMCP Adapter) | Option 4 (Native Implementation) |
|-----------|---------------------------|------------------------------|---------------------------|----------------------------------|
| Protocol Compliance | Good | Good | Excellent | Good |
| Backward Compatibility | Excellent | Good | Good | Excellent |
| Performance | Good | Good | Good | Good |
| Security | Good | Good | Good | Good |
| Maintainability | Fair | Good | Fair | Fair |
| Ecosystem Compatibility | Good | Fair | Excellent | Good |
| **Implementation Effort** | Medium | Medium | Medium-High | High |

---

## Recommendation

### Recommended Option

**Option 2: Separate MCPSkillResourceProvider**

### Justification

Option 2 provides the best balance of:

1. **Clean Architecture**: Maintains separation between local filesystem skills (SkillsRegistry) and remote MCP skills (MCPSkillResourceProvider)
2. **Minimal Risk**: Does not modify existing SkillsRegistry, reducing regression risk
3. **Flexibility**: Easy to add/remove MCP skill sources without affecting local skills
4. **Pattern Consistency**: Follows existing ResourceProvider pattern used for MCP tools/prompts
5. **Future Extensibility**: Can later merge with Option 1 if unified interface is needed

The key insight is that MCP skills are conceptually different from local skills:
- Local skills are editable, development-oriented
- MCP skills are read-only, distribution-oriented

Keeping them separate respects this distinction while still allowing unified access via slash commands.

### Accepted Trade-offs

1. **Dual Skill Sources**: Consumers may need to check both SkillsRegistry and MCPSkillResourceProvider. Mitigation: Create a unified facade for slash commands.

2. **Potential Duplication**: Skills may exist in both local and MCP sources. Mitigation: Prefer local skills when names conflict.

### Conditions

- Must maintain backward compatibility with existing skill commands
- MCP skills are read-only (no write operations via MCP)
- Skill name conflicts resolved in favor of local skills

---

## Technical Design

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        AgentPool                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────┐    ┌──────────────────────────────────┐   │
│  │  SkillsRegistry │    │     MCPSkillResourceProvider     │   │
│  │  (filesystem)   │    │         (new component)          │   │
│  │                 │    │                                  │   │
│  │  Local skills   │    │  ┌──────────────────────────┐   │   │
│  │  ~/.claude/     │    │  │    MCPManager            │   │   │
│  │  .claude/       │    │  │  ┌────────────────────┐  │   │   │
│  │                 │    │  │  │ MCPResourceProvider│  │   │   │
│  └────────┬────────┘    │  │  │ (external servers) │  │   │   │
│           │             │  │  └────────────────────┘  │   │   │
│           │             │  └──────────────────────────┘   │   │
│           │             │                                  │   │
│           │             │  • skill:// URI handling         │   │
│           │             │  • Manifest generation           │   │
│           │             │  • Content caching               │   │
│           │             └──────────────────────────────────┘   │
│           │                                                      │
│           └──────────────┬──────────────────────────────────────┘
│                          │
│                          ▼
│              ┌───────────────────────┐
│              │  UnifiedSkillFacade   │
│              │    (slash commands)   │
│              └───────────┬───────────┘
│                          │
├──────────────────────────┼───────────────────────────────────────┤
│                          ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    MCPServer                             │   │
│  │                                                          │   │
│  │  • Exposes local skills as skill:// resources           │   │
│  │  • Handles list_resources(), read_resource()            │   │
│  │  • Generates manifests with file hashes                 │   │
│  │                                                          │   │
│  │  Resources:                                              │   │
│  │  • skill://{name}/SKILL.md                              │   │
│  │  • skill://{name}/_manifest                             │   │
│  │  • skill://{name}/{supporting-files}                    │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

#### 1. MCPSkillResourceProvider

New ResourceProvider implementation for MCP-sourced skills.

```python
# src/agentpool/resource_providers/mcp_skill_provider.py

class MCPSkillResourceProvider(ResourceProvider):
    """Provider that exposes skills from MCP servers as resources."""

    kind = "mcp_skills"

    def __init__(
        self,
        mcp_manager: MCPManager,
        name: str = "mcp_skills",
        owner: str | None = None,
    ) -> None:
        self.mcp_manager = mcp_manager
        super().__init__(name, owner=owner)
        self._skills_cache: dict[str, Skill] | None = None

    async def get_skills(self) -> list[Skill]:
        """Get skills from all connected MCP servers."""
        if self._skills_cache is not None:
            return list(self._skills_cache.values())

        skills: dict[str, Skill] = {}
        for provider in self.mcp_manager.get_mcp_providers():
            try:
                mcp_skills = await self._fetch_skills_from_provider(provider)
                for skill in mcp_skills:
                    # Prefer local skills on conflict
                    if skill.name not in skills:
                        skills[skill.name] = skill
            except Exception:
                logger.exception("Failed to fetch skills from provider", provider=provider.name)

        self._skills_cache = skills
        return list(skills.values())

    async def _fetch_skills_from_provider(
        self, provider: MCPResourceProvider
    ) -> list[Skill]:
        """Fetch and parse skills from an MCP provider."""
        # Implementation details...
```

#### 2. SkillsResourceProvider (for MCPServer)

ResourceProvider implementation that exposes local skills via MCP protocol.

```python
# src/agentpool/resource_providers/skills_resource_provider.py

class SkillsResourceProvider(ResourceProvider):
    """Provider that exposes local skills as MCP resources."""

    kind = "skills_resources"

    def __init__(
        self,
        skills_registry: SkillsRegistry,
        name: str = "skills_resources",
    ) -> None:
        self.skills_registry = skills_registry
        super().__init__(name)

    async def get_resources(self) -> list[ResourceInfo]:
        """Get skill resources as MCP ResourceInfo objects."""
        resources: list[ResourceInfo] = []

        for skill_name in self.skills_registry.list_items():
            skill = self.skills_registry.get(skill_name)

            # Main SKILL.md resource
            resources.append(ResourceInfo(
                name=f"{skill_name}/SKILL.md",
                uri=f"skill://{skill_name}/SKILL.md",
                description=skill.description,
                mime_type="text/markdown",
            ))

            # Manifest resource
            resources.append(ResourceInfo(
                name=f"{skill_name}/_manifest",
                uri=f"skill://{skill_name}/_manifest",
                description=f"Manifest for {skill_name} skill",
                mime_type="application/json",
            ))

            # Supporting files (as templates)
            # ...discovered from skill directory

        return resources

    async def read_resource(self, uri: str) -> list[str]:
        """Read skill resource content by URI."""
        parsed = self._parse_skill_uri(uri)
        skill = self.skills_registry.get(parsed.skill_name)

        if parsed.path == "SKILL.md":
            content = (skill.skill_path / "SKILL.md").read_text()
            return [content]
        elif parsed.path == "_manifest":
            manifest = await self._generate_manifest(skill)
            return [json.dumps(manifest)]
        else:
            # Supporting file
            content = (skill.skill_path / parsed.path).read_text()
            return [content]

    async def _generate_manifest(self, skill: Skill) -> dict[str, Any]:
        """Generate manifest with file hashes."""
        files: list[dict[str, Any]] = []

        for file_path in skill.skill_path.rglob("*"):
            if file_path.is_file():
                content = file_path.read_bytes()
                files.append({
                    "path": str(file_path.relative_to(skill.skill_path)),
                    "size": len(content),
                    "hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
                })

        return {"skill": skill.name, "files": files}
```

#### 3. MCPServer Integration

Extend the existing MCPServer to register skill resources.

```python
# src/agentpool_server/mcp_server/server.py

class MCPServer(BaseServer):
    def __init__(
        self,
        pool: AgentPool[Any],
        config: MCPPoolServerConfig,
        # ... existing params
        skills_registry: SkillsRegistry | None = None,  # NEW
    ) -> None:
        # ... existing init

        # NEW: Add skills resource provider
        if skills_registry:
            self.skills_provider = SkillsResourceProvider(skills_registry)
            self._register_skills_resources()

    def _register_skills_resources(self) -> None:
        """Register skill resources with FastMCP."""

        @self.fastmcp.resource("skill://{skill_name}/{file_path}")
        async def get_skill_resource(skill_name: str, file_path: str) -> str:
            """Read a skill resource by URI."""
            uri = f"skill://{skill_name}/{file_path}"
            result = await self.skills_provider.read_resource(uri)
            return result[0] if result else ""
```

#### 4. Unified Skill Access

Create a facade that merges local and MCP skills for slash commands.

```python
# src/agentpool/skills/unified_facade.py

class UnifiedSkillFacade:
    """Unified interface for accessing both local and MCP skills."""

    def __init__(
        self,
        skills_registry: SkillsRegistry,
        mcp_skill_provider: MCPSkillResourceProvider | None = None,
    ) -> None:
        self.local_registry = skills_registry
        self.mcp_provider = mcp_skill_provider

    async def get_skill(self, name: str) -> Skill | None:
        """Get skill by name, preferring local over MCP."""
        # Try local first
        try:
            return self.local_registry.get(name)
        except ToolError:
            pass

        # Try MCP
        if self.mcp_provider:
            mcp_skills = await self.mcp_provider.get_skills()
            for skill in mcp_skills:
                if skill.name == name:
                    return skill

        return None

    async def list_all_skills(self) -> list[Skill]:
        """List all skills from all sources."""
        local_skills = [
            self.local_registry.get(name)
            for name in self.local_registry.list_items()
        ]

        if self.mcp_provider:
            mcp_skills = await self.mcp_provider.get_skills()
            # Merge, preferring local on name conflict
            local_names = {s.name for s in local_skills}
            merged = local_skills + [s for s in mcp_skills if s.name not in local_names]
            return merged

        return local_skills
```

### Data Model

#### SkillURI

```python
@dataclass
class SkillURI:
    """Parsed skill:// URI."""

    skill_name: str
    path: str

    @classmethod
    def parse(cls, uri: str) -> SkillURI:
        """Parse a skill:// URI."""
        if not uri.startswith("skill://"):
            raise ValueError(f"Not a skill URI: {uri}")

        parts = uri[8:].split("/", 1)  # Remove "skill://"
        skill_name = parts[0]
        path = parts[1] if len(parts) > 1 else "SKILL.md"

        return cls(skill_name=skill_name, path=path)

    def __str__(self) -> str:
        return f"skill://{self.skill_name}/{self.path}"
```

#### SkillManifest

```python
class SkillFileInfo(TypedDict):
    path: str
    size: int
    hash: str

class SkillManifest(TypedDict):
    skill: str
    files: list[SkillFileInfo]
```

### Resource Template Configuration

Following FastMCP's pattern, supporting files can be exposed as templates:

```python
# Mode 1: Template (default) - supporting files via template
@mcp.resource("skill://{skill_name}/{file_path:path}")
async def get_skill_file(skill_name: str, file_path: str) -> str:
    ...

# Mode 2: Resources - all files listed individually
# Set supporting_files="resources" to list all files
```

---

## Security Considerations

### Threat Analysis

| Threat | Impact | Likelihood | Mitigation |
|--------|--------|------------|------------|
| **Path Traversal** | High | Medium | Validate skill_name and file_path, reject `..` segments |
| **Malicious Skill Content** | Medium | Medium | Skills are text-only, no code execution; validate MIME types |
| **Information Disclosure** | High | Low | Only expose files within skill directories, not arbitrary paths |
| **DoS via Large Files** | Medium | Low | Implement size limits (e.g., 10MB max per file) |
| **Hash Collision** | Low | Low | Use SHA-256 for file hashes |

### Security Measures

- [ ] Path validation: Reject URIs with `..` or absolute paths
- [ ] File size limits: Max 10MB per file, max 100 files per skill
- [ ] MIME type validation: Only allow safe types (text/*, application/json)
- [ ] Sandbox: Skills cannot access files outside their directory
- [ ] Audit logging: Log all skill resource access

---

## Implementation Plan

### Phase 1: Core Infrastructure

**Scope**: Basic skill resource exposure via MCP

**Deliverables**:
- `SkillsResourceProvider` class
- `SkillURI` parsing
- Manifest generation with SHA-256 hashes
- MCPServer integration

**Dependencies**: None

### Phase 2: MCP Skill Consumption

**Scope**: Consume skills from external MCP servers

**Deliverables**:
- `MCPSkillResourceProvider` class
- Skill caching and refresh
- Unified skill facade for slash commands

**Dependencies**: Phase 1

### Phase 3: Integration & Polish

**Scope**: Testing, documentation, edge cases

**Deliverables**:
- Comprehensive tests
- Documentation
- Performance optimization
- Security audit

**Dependencies**: Phase 2

### Milestones

| Milestone | Description | Target | Status |
|-----------|-------------|--------|--------|
| Phase 1 Complete | Local skills exposed via MCP | T+2 weeks | Not Started |
| Phase 2 Complete | MCP skill consumption working | T+4 weeks | Not Started |
| Phase 3 Complete | Production ready | T+6 weeks | Not Started |

### Rollback Strategy

If issues arise:
1. Feature flags disable skill resources in MCPServer
2. MCPSkillResourceProvider can be removed from provider chain
3. SkillsRegistry continues to work independently

---

## Open Questions

1. **Should we support skill write operations?**
   - Context: Currently read-only. Write would allow skill creation via MCP.
   - Owner: RFC Reviewers
   - Status: Open

2. **How should we handle skill updates?**
   - Context: MCP skills may change; need cache invalidation strategy.
   - Owner: Implementation Team
   - Status: Open

3. **Should we support skill dependencies?**
   - Context: Skills may reference other skills; how to resolve?
   - Owner: Architecture Team
   - Status: Open

4. **What is the maximum skill size?**
   - Context: Need limits to prevent abuse.
   - Owner: Security Review
   - Status: Open (proposal: 10MB per file, 100MB per skill)

---

## Decision Record

> To be completed after RFC review.

### Decision

**Status**: PENDING

**Date**: TBD

**Approvers**: TBD

### Decision Summary

TBD

### Key Discussion Points

TBD

### Conditions of Approval

TBD

### Dissenting Opinions

TBD

---

## References

### Related Documents

- [FastMCP Skills Provider](https://gofastmcp.com/servers/providers/skills)
- AgentPool Skills Documentation
- MCP Protocol Specification

### External Resources

- [Anthropic Skills Repository](https://github.com/anthropics/skills)
- [Agent Skills Spec](https://github.com/agentskills/agentskills)

### Appendix

#### Skill Manifest Example

```json
{
  "skill": "code-review",
  "files": [
    {
      "path": "SKILL.md",
      "size": 1523,
      "hash": "sha256:a1b2c3d4e5f6..."
    },
    {
      "path": "examples/python.md",
      "size": 890,
      "hash": "sha256:f6e5d4c3b2a1..."
    }
  ]
}
```

#### MCP Resource URI Examples

```
skill://code-review/SKILL.md
skill://code-review/_manifest
skill://code-review/examples/python.md
```

#### File Structure

```
src/agentpool/
├── skills/
│   ├── __init__.py
│   ├── skill.py
│   ├── registry.py
│   ├── command.py
│   ├── command_registry.py
│   ├── manager.py
│   └── unified_facade.py          # NEW
└── resource_providers/
    ├── __init__.py
    ├── base.py
    ├── pool.py
    ├── mcp_provider.py
    ├── skills_instruction.py
    ├── skills_resource_provider.py  # NEW
    └── mcp_skill_provider.py        # NEW

src/agentpool_server/
└── mcp_server/
    ├── server.py                    # MODIFIED
    └── ...
```
