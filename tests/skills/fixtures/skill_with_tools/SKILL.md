---
# ============================================================
# SKILL.md Frontmatter — Tools
# ============================================================
# This fixture demonstrates tools frontmatter configuration.
#
# tools is a list of SkillToolConfig objects. Each tool has:
#   - type: always "python" (future: "docker", "subprocess", etc.)
#   - import_path: dotted Python path in "module:function" format
#
# Tools are imported lazily via importlib at runtime.
# ============================================================

name: skill-with-tools
description: A test skill with multiple Python tool examples in frontmatter

# Tool configurations for skill-provided functionality.
# Each entry defines a callable Python function exposed as a tool.
tools:
  # Tool 1: Simple stdlib function — JSON parsing
  - type: python
    import_path: "json:loads"                # "module:function" format

  # Tool 2: Path manipulation from os.path
  - type: python
    import_path: "os.path:join"              # Dotted module: os.path is a submodule

  # Tool 3: Get current working directory
  - type: python
    import_path: "os:getcwd"

  # Tool 4: Class constructor (datetime)
  - type: python
    import_path: "datetime:datetime"         # Classes work if callable
---

# Skill with Tools

This skill demonstrates `tools` frontmatter configuration with multiple Python callable tools.

## Format

The `import_path` follows Python's standard import format:

```
package.module:function
package.submodule:function
module:ClassName
```

## Tools

| Import Path        | Description                     |
|--------------------|---------------------------------|
| `json:loads`       | Parse JSON string to dict       |
| `os.path:join`     | Join path components            |
| `os:getcwd`        | Get current working directory   |
| `datetime:datetime`| Create datetime instances       |

## Usage

Use this skill when testing Python tool imports via `SkillToolManager.import_tools()`.
