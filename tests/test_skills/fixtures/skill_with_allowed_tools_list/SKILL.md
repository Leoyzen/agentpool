---
# ============================================================
# SKILL.md Frontmatter — allowed-tools (list format)
# ============================================================
# This fixture demonstrates allowed-tools as a YAML list.
#
# allowed-tools accepts TWO formats:
#   1) YAML list (this fixture):
#        allowed-tools:
#          - bash
#          - read
#
#   2) Space-separated string (see skill-with-allowed-tools-string):
#        allowed-tools: "bash read grep"
#
# The @field_validator normalizes list[str] to space-separated str
# internally, so both formats work identically at runtime.
# ============================================================

name: skill-with-allowed-tools-list
description: A test skill that declares allowed-tools as a YAML list

# allowed-tools restricts which tools the skill's instructions may use.
# When set, the skill can ONLY invoke the listed tools.
# Use a YAML list for readability with multiple entries.
allowed-tools:
  - bash         # Shell command execution
  - read         # File reading
  - grep         # Content search
  - glob         # File pattern matching
  - write        # File writing
  - edit         # File editing
---

# Skill with Allowed Tools as List

This skill demonstrates `allowed-tools` declared as a YAML **list** (not a space-separated string).

## Behavior

When `allowed-tools` is set, the skill's instructions are wrapped in a filtering wrapper
that only allows the listed tools to be invoked. Tools not in the list are blocked.

## Both Formats

| Format | Example                                  |
|--------|------------------------------------------|
| List   | `["bash", "read", "grep"]` (this file)   |
| String | `"bash read grep"` (complementary fixture)|

## Usage

Use this skill when testing the `allowed-tools` list normalization validator.
