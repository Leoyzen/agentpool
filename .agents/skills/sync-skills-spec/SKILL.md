---
name: sync-skills-spec
description: Sync the agentpool skills implementation with the official Agent Skills Spec by comparing against the reference repository.
---

# Sync Skills Spec

Keep `src/agentpool/skills/` aligned with the official Agent Skills Spec.

## Steps

1. **Clone the reference repo** into a temporary directory:
   ```bash
   tmp=$(mktemp -d)
   git clone --depth 1 https://github.com/agentskills/agentskills "$tmp/agentskills"
   ```

2. **Read the spec and reference implementation**:
   - `$tmp/agentskills/spec/` — the formal specification documents
   - `$tmp/agentskills/implementations/` — reference implementations (especially Python)
   - Focus on the `SkillProperties` model, frontmatter fields, validation rules, and prompt generation

3. **Compare with our implementation**:
   - `src/agentpool/skills/skill.py` — our `Skill` Pydantic model, validators, `parse_frontmatter()`, `to_prompt()`
   - `src/agentpool/skills/registry.py` — discovery and registration (our extension, not part of the spec)
   - `src/agentpool/skills/manager.py` — pool-wide management (our extension)
   - Check for missing fields, changed validation rules, new spec requirements

4. **Apply updates** to our code:
   - Add any new frontmatter fields to the `Skill` model
   - Update validators to match spec changes (name format, length limits, allowed fields)
   - Update `to_prompt()` if the recommended XML format changed
   - Do NOT replace our registry/manager — those are agentpool-specific extensions

5. **Update the synced commit hash** in `src/agentpool/skills/skill.py`:
   - Get the current HEAD of the cloned repo: `git -C "$tmp/agentskills" rev-parse HEAD`
   - Update the `SPEC_SYNCED_COMMIT` constant to the new hash
   - This tracks which version of the spec we last synced against

6. **Clean up**:
   ```bash
   rm -rf "$tmp"
   ```

## Key differences from upstream

Our implementation extends the spec with:
- **Async discovery** via fsspec/UPath (supports GitHub, S3, etc.)
- **Registry pattern** (`SkillsRegistry` extending `BaseRegistry`)
- **Pool-wide management** (`SkillsManager` with async context manager)
- **YAML parsing** via `yamling` (not `strictyaml`)

These are intentional and should be preserved.
