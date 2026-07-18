---
# ============================================================
# SKILL.md Frontmatter — allowed-tools (string format)
# ============================================================
# This fixture demonstrates allowed-tools as a space-separated string.
#
# allowed-tools accepts TWO formats:
#   1) Space-separated string (this fixture):
#        allowed-tools: "bash read grep"
#
#   2) YAML list (see skill-with-allowed-tools-list):
#        allowed-tools:
#          - bash
#          - read
#
# The string format is the internal representation; the YAML list is
# normalized to string by the @field_validator(mode="before").
# ============================================================

name: skill-with-allowed-tools-string
description: A test skill that declares allowed-tools as a space-separated string

# allowed-tools restricts which tools the skill's instructions may use.
# Use a space-separated string for compact YAML with few entries.
allowed-tools: "bash read grep write edit"
---

# Skill with Allowed Tools as String

This skill demonstrates `allowed-tools` declared as a **space-separated string** (the traditional format).

## Behavior

Same as the list format — restricts the skill to only the listed tools.

## Both Formats

| Format | Example                                       |
|--------|-----------------------------------------------|
| String | `"bash read grep"` (this file)                |
| List   | `["bash", "read", "grep"]` (complementary)    |

## Usage

Use this skill when testing the `allowed-tools` string format parsing.
