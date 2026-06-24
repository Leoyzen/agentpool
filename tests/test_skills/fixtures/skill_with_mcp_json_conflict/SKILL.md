---
# ============================================================
# SKILL.md Frontmatter -- mcp.json conflict (precedence test)
# ============================================================
# This fixture has BOTH frontmatter mcp-servers AND a companion mcp.json.
#
# This tests the precedence rule:
#   mcp.json FULLY REPLACES frontmatter mcp-servers.
#
# Frontmatter defines: frontmatter-server (python http.server)
# mcp.json defines:    mcp-json-server (npx playwright mcp)
#
# When loaded, mcp.json's entire mcpServers dict replaces the
# frontmatter mcp-servers. The frontmatter servers are LOST.
# ============================================================

name: skill-with-mcp-json-conflict
description: A test skill with BOTH frontmatter mcp-servers AND a companion mcp.json (tests mcp.json precedence)

# Frontmatter mcp-servers — will be OVERRIDDEN by mcp.json for colliding keys.
# If no mcp.json existed, this would be the sole configuration.
mcp-servers:
  frontmatter-server:
    command: python
    args: ["-m", "http.server", "8080"]
---

# Skill with mcp.json Conflict

This skill has both `mcp-servers` in frontmatter and a companion `mcp.json` file.
The mcp.json companion takes precedence when keys collide.

## Expected Behavior

| Source          | Server Name          | Command        |
|-----------------|----------------------|----------------|
| Frontmatter     | frontmatter-server   | python ...     |
| mcp.json        | mcp-json-server      | npx playwright |
| **Result**      | Only mcp-json-server | (full override)|

## Verification

```python
skill = Skill.from_skill_dir(UPath("skill_with_mcp_json_conflict"))
# mcp_servers contains ONLY mcp.json servers (full override)
assert "mcp-json-server" in skill.mcp_servers
assert "frontmatter-server" not in skill.mcp_servers
```

## Usage

Use this skill when testing mcp.json precedence over frontmatter mcp-servers.
