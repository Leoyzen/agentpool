---
title: config
description: Configuration management and diagnostics
icon: material/cog
---

The `config` command group helps you understand and manage AgentPool's layered configuration system.

## Overview

AgentPool automatically discovers and merges configuration from multiple sources:

1. **Global config** (`~/.config/agentwolf/agentwolf.yml`)
2. **Custom config** (`AGENTPOOL_CONFIG` environment variable)
3. **Project config** (`agentwolf.yml` in project/git root)
4. **Explicit config** (CLI argument)

These commands help you inspect which configs are being loaded and create new ones.

## Commands

The `config` command group includes the following commands:

```bash
# Show current configuration
agentwolf config show [config_path]

# Show config search paths
agentwolf config paths

# Initialize a new configuration
agentwolf config init [path] [--force]
```

### config show

Display the current configuration, showing which config files are found and what they contain.

```bash
# Show merged configuration
agentwolf config show

# Show with a specific explicit config
agentwolf config show my-agents.yml

# Output as YAML
agentwolf config show --format yaml
```

### config paths

Display the paths AgentPool searches for configuration files.

```bash
agentwolf config paths
```

### config init

Create a new configuration file.

```bash
# Create a starter config in current project
agentwolf config init

# Create global config for user-wide preferences
agentwolf config init global

# Create at a specific path
agentwolf config init ./configs/my-agents.yml

# Overwrite existing config
agentwolf config init --force
```

## Examples

### Inspect Configuration

```bash
# Show which config files are found and what they contain
agentwolf config show

# Show with a specific explicit config included
agentwolf config show my-agents.yml

# Output as YAML for scripting
agentwolf config show --format yaml
```

### View Config Paths

```bash
# Show where AgentPool looks for config files
agentwolf config paths
```

### Create New Config

```bash
# Create a starter config in current project
agentwolf config init

# Create a global config for user-wide preferences
agentwolf config init global

# Create at a specific path
agentwolf config init ./configs/my-agents.yml

# Overwrite existing config
agentwolf config init --force
```

## Use Cases

### Setting Global Preferences

Create a global config to set preferences that apply to all projects:

```bash
agentwolf config init global
```

Then edit `~/.config/agentwolf/agentwolf.yml`:

```yaml
# Global preferences
model_variants:
  fast:
    type: string
    identifier: openai:gpt-4o-mini
  smart:
    type: anthropic
    identifier: claude-sonnet-4-5

storage:
  provider: sql
  database_url: sqlite:///~/.local/share/agentwolf/history.db
```

### Project-Specific Agents

Create a project config that inherits global settings:

```bash
agentwolf config init
```

Then edit `./agentwolf.yml`:

```yaml
agents:
  coder:
    model: smart  # Uses global model_variant
    system_prompt: "You are an expert in this codebase."
    tools:
      - type: file_access
      - type: bash
```

### Debugging Config Issues

If your agent isn't behaving as expected, check which configs are being loaded:

```bash
agentwolf config show
```

This shows:
- Which config files were found
- What keys each layer contributes
- The final merged result
