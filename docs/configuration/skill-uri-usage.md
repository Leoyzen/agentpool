---
title: Skill URI Usage
description: Using skill:// URIs to load skills and reference content
icon: material/link-variant
order: 11
---

# Skill URI Usage

AgentPool supports a unified `skill://` URI scheme for accessing skills from both local filesystem and MCP servers. This enables consistent skill loading regardless of where skills are stored.

## Overview

The skill URI system provides:

- **Unified access**: Load skills from local directories or MCP servers using the same interface
- **Reference content**: Access supporting files bundled with skills
- **Argument substitution**: Pass arguments to skills with bash-style variables
- **Provider priority**: Automatic resolution when multiple sources have the same skill name

## URI Format

```
skill://{provider}/{skill-name}/{reference-path}
```

| Component | Description | Example |
|-----------|-------------|---------|
| `provider` | Skill source identifier | `local`, `github-copilot` |
| `skill-name` | Name of the skill | `python-expert` |
| `reference-path` | Optional path to supporting files | `references/style-guide.md` |

## Loading Skills

### By Short Name (Auto-Routing)

When you use a bare skill name, AgentPool searches all providers in priority order:

```python
from agentpool import AgentPool

async with AgentPool("config.yml") as pool:
    agent = pool.get_agent("assistant")
    # Agent can use: load_skill(ctx, "python-expert")
    # Automatically finds skill across all providers
```

**Priority order**: Local skills first, then MCP providers in registration order.

### By Full URI (Explicit Provider)

For precise control over which provider to use:

```python
# Local filesystem skill
await load_skill(ctx, "skill://local/python-expert")

# MCP server skill
await load_skill(ctx, "skill://github-copilot/code-review")
```

### Loading Reference Content

Skills can bundle supporting files in a `references/` directory:

```python
# Load a reference file from a local skill
await load_skill(ctx, "skill://local/python-expert/references/pep8-guide.md")

# Load from MCP resource-based skill
await load_skill(ctx, "skill://skills-server/pdf-processing/examples/sample.pdf")
```

## URI Examples

### Local Filesystem Skills

```
skill://local/python-expert                          # Main skill
skill://local/python-expert/SKILL.md                 # Explicit main file
skill://local/python-expert/references/style-guide.md # Reference file
skill://local/my%20skill                             # URL-encoded name
```

### MCP Prompt-Based Skills

```
skill://github-copilot/code-review     # Prompt exposed as skill
skill://my-mcp/custom-prompt           # Custom MCP server prompt
```

### MCP Resource-Based Skills (FastMCP Skills Provider)

```
skill://skills-server/pdf-processing                 # Short form
skill://skills-server/pdf-processing/SKILL.md        # Main skill file
skill://skills-server/pdf-processing/_manifest       # JSON manifest
skill://skills-server/pdf-processing/examples/doc.pdf # Reference
```

## Argument Substitution

Skills support bash-style variable substitution when arguments are provided:

| Variable | Description | Example |
|----------|-------------|---------|
| `$1`, `$2`, ... | Positional arguments | `$1` becomes first argument |
| `$@` | All arguments | All arguments as single string |
| `$ARGUMENTS` | All arguments | Alias for `$@` |

### Example Skill with Arguments

```markdown
# Skill: greeting

Generate a personalized greeting.

## Instructions

Create a greeting for $1 from $2.
Use a $3 tone.

## Allowed Tools

generate_text
```

### Using Arguments

```python
# Arguments are passed as a string
await load_skill(ctx, "greeting", "Alice Company formal")

# Result substitutes:
# $1 → "Alice"
# $2 → "Company"
# $3 → "formal"
```

## Creating Skills with References

To create a skill with supporting files:

1. Create the skill directory:
   ```
   ~/.claude/skills/my-skill/
   ```

2. Add the main `SKILL.md`:
   ```markdown
   # Skill: my-skill
   
   Description of what this skill does.
   
   ## License
   MIT
   
   ## Compatibility
   1.0.0
   
   ## Allowed Tools
   bash, read, grep
   
   ## Instructions
   Detailed instructions here...
   ```

3. Create a `references/` subdirectory:
   ```
   ~/.claude/skills/my-skill/references/
   ```

4. Add reference files:
   ```
   ~/.claude/skills/my-skill/references/guide.md
   ~/.claude/skills/my-skill/references/examples/
   ~/.claude/skills/my-skill/references/templates/
   ```

5. Access via URI:
   ```python
   await load_skill(ctx, "skill://local/my-skill/references/guide.md")
   ```

## MCP Skills Provider Protocol

AgentPool supports the [FastMCP Skills Provider protocol](https://gofastmcp.com/servers/providers/skills), allowing MCP servers to expose skills as resources.

### Resource Patterns

When an MCP server implements the Skills Provider protocol:

| URI Pattern | Purpose |
|-------------|---------|
| `skill://{server}/{skill}` | Short form (resolves to main skill) |
| `skill://{server}/{skill}/SKILL.md` | Main instruction file |
| `skill://{server}/{skill}/_manifest` | JSON manifest with file list |
| `skill://{server}/{skill}/{file}` | Supporting/reference files |

### Configuration Example

```yaml
mcp_servers:
  - "uvx mcp-server-with-skills"

agents:
  assistant:
    model: openai:gpt-4o
    tools:
      - type: skills
    # Can now load skills from MCP server:
    # skill://mcp-server-with-skills/pdf-processing
```

## Security Considerations

The skill URI system includes several security protections:

### Path Traversal Protection

- `..` components in paths are rejected
- Paths are resolved and verified to be within allowed directories
- Symlinks are resolved before validation

### Provider Name Validation

- Must start with alphanumeric character
- Can contain alphanumeric, hyphen, and underscore
- Maximum 63 characters

### Null Byte Protection

- Null bytes (`\x00`) in paths are rejected

## Provider Priority and Collision Resolution

When multiple providers have skills with the same name:

1. **Local provider** always has highest priority
2. **MCP providers** are checked in registration order
3. Collisions are logged with the selected provider noted

### Example

```
Local provider:      python-expert, refactoring
MCP provider A:      code-review, python-expert
MCP provider B:      documentation, code-review

Resolution:
- python-expert → local (priority)
- refactoring → local
- code-review → MCP provider A (first registered)
- documentation → MCP provider B
```

## Listing Available Skills

Use the `list_skills` tool to see all available skills:

```python
result = await list_skills(ctx)
print(result)
```

Output format:
```
Available skills:

## local (3 skills)
- **python-expert**: Expert Python development techniques
  URI: `skill://local/python-expert`
- **refactoring**: Safe code refactoring patterns
  URI: `skill://local/refactoring`

## github-copilot (2 skills)
- **code-review**: Automated code review
  URI: `skill://github-copilot/code-review`
```

## Configuration Reference

Enable skill loading in your agent configuration:

```yaml
agents:
  my_agent:
    model: openai:gpt-4o
    tools:
      - type: skills
        # Optional: limit number of skills shown in listings
        max_skills: 20
```

See [Skills Configuration](./skills.md) for detailed configuration options.

## Migration Guide

### From Bare Skill Names

Existing code using bare skill names continues to work:

```python
# Before (still works)
await load_skill(ctx, "python-expert")

# After (new option)
await load_skill(ctx, "skill://local/python-expert")
```

### New Capabilities

New features available with RFC-0020:

1. **Explicit provider selection**: Use full URIs when multiple providers have the same skill
2. **Reference content**: Access supporting files via URI paths
3. **MCP skills**: Load skills from MCP servers using the same interface
4. **Argument substitution**: Pass dynamic arguments to skills

## Troubleshooting

### Skill Not Found

```
Skill not found: 'my-skill'. Available: python-expert, refactoring
```

- Check skill name spelling
- Verify skill exists with `list_skills`
- Check provider name if using full URI

### Reference Not Found

```
Reference not found: 'guide.md' in skill 'my-skill'
```

- Verify reference file exists in skill's `references/` directory
- Check for typos in the reference path

### Security Error

```
Security error: Path traversal detected in URI
```

- Remove `..` components from paths
- Ensure path does not escape the skill directory
