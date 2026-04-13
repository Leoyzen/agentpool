---
title: Skill URI Loading
description: Loading skills using the skill:// URI scheme
icon: material/link-variant
hide:
  - toc
---

# Skill URI Loading

This example demonstrates how to load skills using the `skill://` URI scheme introduced in RFC-0020.

## Overview

The skill URI system provides unified access to skills from both local filesystem and MCP servers:

- **Short name loading**: `load_skill(ctx, "python-expert")` - auto-routes to first provider
- **Full URI loading**: `load_skill(ctx, "skill://local/python-expert")` - explicit provider selection
- **Reference loading**: `load_skill(ctx, "skill://local/python-expert/references/guide.md")`
- **Argument substitution**: Pass arguments like `load_skill(ctx, "greeting", "Alice Company formal")`

## Files

- `config.yml` - Agent configuration with skills tool enabled
- `skills/greeting/SKILL.md` - Example skill with argument substitution

## Configuration

```yaml
skills:
  paths:
    - ./skills  # Local skills directory

agents:
  skill_loader:
    type: native
    model: openai:gpt-4o-mini
    tools:
      - type: skills  # Enables load_skill and list_skills tools
```

## Usage Examples

### Load by Short Name

The simplest approach uses the skill name directly:

```python
await load_skill(ctx, "greeting")
```

This auto-routes to the first provider that has a skill named "greeting".

### Load by Full URI

For explicit provider selection:

```python
await load_skill(ctx, "skill://local/greeting")
```

This ensures you get the skill from the "local" provider specifically.

### Load with Arguments

Arguments support bash-style substitution:

```python
await load_skill(ctx, "greeting", "Alice Company formal")
```

The skill content can use:
- `$1` → "Alice"
- `$2` → "Company"
- `$3` → "formal"
- `$@` or `$ARGUMENTS` → "Alice Company formal"

### List Available Skills

```python
await list_skills(ctx)
```

Returns all skills from all providers with their URIs:

```
Available skills:

## local (1 skills)
- **greeting**: Generate personalized greetings
  URI: `skill://local/greeting`
```

## How It Works

1. **Skill Discovery**: AgentPool scans configured paths for SKILL.md files
2. **Provider Registration**: Local skills are registered under the "local" provider
3. **URI Resolution**: Short names are resolved using provider priority (local first)
4. **Argument Substitution**: Variables like `$1`, `$@` are replaced before returning content

## Running the Example

```bash
# List available skills
agentpool run skill_uri_loading/skill_lister "List all available skills"

# Load a skill by short name
agentpool run skill_uri_loading/skill_loader "Load the greeting skill"

# Load with arguments
agentpool run skill_uri_loading/skill_loader 'Load greeting with "Alice Company formal"'

# Load by full URI
agentpool run skill_uri_loading/skill_loader "Load skill://local/greeting"
```

## URI Format Reference

```
skill://{provider}/{skill-name}                    # Short form
skill://{provider}/{skill-name}/SKILL.md           # Explicit main file
skill://{provider}/{skill-name}/references/file    # Reference files
```

See [Skill URI Usage](../../configuration/skill-uri-usage.md) for complete documentation.
