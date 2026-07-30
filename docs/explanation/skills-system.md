# Skills System

Skills are defined as `SKILL.md` files following the [Agent Skills Spec](https://github.com/agentskills/agentskills). They are discovered, loaded, and injected into agent prompts.

## Skill Locations (Three Tiers)

- `~/.claude/skills/*/SKILL.md` — User-wide (default)
- `.claude/skills/*/SKILL.md` — Project-wide (e.g., `openspec-*`)
- `.agents/skills/*/SKILL.md` — Agent/workflow skills
- MCP servers via `skill://` resource URIs

## Key Files

- `src/agentpool/skills/skill.py` — `Skill` model: YAML frontmatter parsing, lazy instruction loading
- `src/agentpool/skills/registry.py` — `SkillsRegistry` auto-discovers SKILL.md files from configured paths
- `src/agentpool/skills/manager.py` — `SkillsManager` pool-level lifecycle
- `src/agentpool/skills/uri_resolver.py` — `skill://` URI scheme resolver
- `src/agentpool/skills/command.py` — `SkillCommand` wraps skills as protocol-agnostic slash commands
- `src/agentpool/skills/instruction_provider.py` — `SkillsInstructionProvider` injects skills as XML into prompts (metadata/full modes) — migrated from `resource_providers/`

## Injection Modes

Via YAML `skills.instruction`:

- `off` — No injection
- `metadata` — `<available-skills>` XML block (names + descriptions)
- `full` — `<skill_content>` XML block with complete instructions + parameters
