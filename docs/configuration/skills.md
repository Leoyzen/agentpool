---
title: Skills Configuration
description: Configure automatic skills injection into agent prompts
icon: material/hammer-wrench
order: 10
---

Skills provide specialized instructions and techniques that agents can follow. AgentPool supports automatic injection of skills into agent system prompts using structured XML formatting.

## Overview

Skills injection allows you to:

- Automatically include relevant skill instructions in agent prompts
- Configure global defaults for all agents
- Override per-agent using skills tool configuration
- Limit the number of skills included to manage token count

## Configuration Structure

### Global Skills Configuration

```yaml
skills:
  # Skill discovery paths
  paths:
    - ~/.config/agentpool/skills
    - ./skills
  
  # Include default AgentPool skills (default: true)
  include_default: true
  
  # Instruction injection configuration
  instruction:
    # Injection mode: off, metadata, or full
    mode: metadata
    # Maximum number of skills to inject (default: 20)
    max_skills: 20
```

### Injection Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `off` | No skill injection (default) | Disable automatic injection |
| `metadata` | Skill names and descriptions only | Quick reference without full content |
| `full` | Complete skill instructions | When skills contain critical instructions |

## Agent-Specific Overrides

Override global settings for specific agents using the skills toolset:

```yaml
agents:
  expert_coder:
    model: openai:gpt-4o
    system_prompt: "You are an expert developer"
    tools:
      - type: skills
        # Override global injection mode for this agent
        injection_mode: full
        max_skills: 10
```

## XML Output Format

When skills injection is enabled, agents receive structured XML in their system prompt:

```xml
<available-skills>
  <skill id="python-style-guide" name="Python Style Guide" description="PEP 8 coding conventions">
    <instructions>
      ## Python Style Guide
      
      Follow PEP 8 conventions:
      - Use 4 spaces for indentation
      - Maximum line length of 88 characters
      - Use snake_case for functions and variables
      - Use PascalCase for classes
    </instructions>
    <base_directory>/home/user/.config/agentpool/skills/python-style-guide/</base_directory>
  </skill>
  <skill id="refactoring" name="Code Refactoring" description="Safe refactoring techniques">
    <instructions>
      ## Code Refactoring
      
      Always follow these steps:
      1. Understand the existing code
      2. Run tests before changes
      3. Make small, focused changes
      4. Run tests after each change
      5. Commit incrementally
    </instructions>
    <base_directory>/home/user/.config/agentpool/skills/refactoring/</base_directory>
  </skill>
</available-skills>
```

## Complete Example

```yaml
# Global skills configuration
skills:
  paths:
    - ~/.config/agentpool/skills
    - ./project-skills
  include_default: true
  
  # Default: metadata-only injection for all agents
  instruction:
    mode: metadata
    max_skills: 20

agents:
  # Appends /skills to tool names (default: false)
  append_tools_namespace: true

  # Standard agent - uses global metadata injection
  assistant:
    model: openai:gpt-4o-mini
    system_prompt: "You are a helpful assistant"
    tools:
      - type: skills
  
  # Expert agent - uses full skill content
  expert:
    model: openai:gpt-4o
    system_prompt: "You are an expert developer"
    tools:
      - type: skills
        injection_mode: full
        max_skills: 10
  
  # Minimal agent - no skills injection
  minimal:
    model: openai:gpt-4o-mini
    system_prompt: "Keep responses brief"
    tools: []  # No skills tool
```

## Backward Compatibility

By default, skills injection is **disabled** (`mode: off`). This ensures:

- Existing configurations continue to work unchanged
- Agents without explicit configuration see no skill injection
- Opt-in required to enable automatic injection

## Related Configuration

- [Toolsets](./node-types/index.md) - Configure agent tools including skills tool
- [Agent Pool](./agent-pool.md) - Global pool configuration

## See Also

- [RFC-0008: Dynamic Skills Injection](../rfcs/accepted/RFC-0008-dynamic-skills-injection.md) - Implementation details
- [Skills Toolset](../core-concepts/toolsets.md) - Skills toolset reference
