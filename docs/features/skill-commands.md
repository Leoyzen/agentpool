# Skill Commands

## Overview

Skill commands allow skills defined in your skills directory to be exposed as slash commands across ACP, AG-UI, and OpenCode protocols. This enables direct skill invocation via protocol-native interfaces.

## What Are Skill Commands?

Skills are reusable instruction sets stored in SKILL.md files. With skill commands, these become directly invocable via:

- **ACP**: AvailableCommand[] in capabilities
- **AG-UI**: Tools with skill__ prefix
- **OpenCode**: slashed Commands with skill: prefix

## Configuration

Skills are automatically discovered from your skills directory and exposed as commands. No additional configuration is required.

### Example SKILL.md
```markdown
# Skill: my-skill
A description of what this skill does

## License
MIT

## Compatibility
1.0.0

## Allowed Tools
bash, read, grep

## Instructions
Detailed instructions for the agent...
```

## Protocol-Specific Usage

### ACP Protocol
Skills appear as AvailableCommand in AgentCapabilities:
```json
{
  "slash_commands": [
    {
      "name": "my-skill",
      "description": "A description...",
      "input": {"hint": "Arguments for skill"}
    }
  ]
}
```

### AG-UI Protocol
Skills appear as Tools with `skill__` prefix:
```json
{
  "name": "skill__my-skill",
  "description": "A description...",
  "parameters": {
    "type": "object",
    "properties": {
      "arguments": {"type": "string"}
    }
  }
}
```

### OpenCode Protocol
Skills appear as Commands with `skill:` prefix:
```
/skill:my-skill arguments here
```

## Troubleshooting

### Skills not appearing
- Ensure SKILL.md files are valid
- Check skills directory path in config
- Verify skills have required metadata (name, description)

### Command not executing
- Check allowed_tools in SKILL.md
- Verify skill instructions are valid
