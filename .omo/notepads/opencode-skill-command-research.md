# OpenCode Skill Command Handling - Research Notes

## Overview

This document summarizes how OpenCode handles slash commands with arguments, specifically for skill execution, to inform RFC-0017 implementation.

---

## 1. Command Parsing (UI Side)

**File**: `packages/app/src/components/prompt-input/submit.ts` (lines 259-288)

```typescript
if (text.startsWith("/")) {
  const [cmdName, ...args] = text.split(" ")
  const commandName = cmdName.slice(1)  // Remove leading "/"
  const customCommand = sync.data.command.find((c) => c.name === commandName)
  if (customCommand) {
    clearInput()
    client.session.command({
      sessionID: session.id,
      command: commandName,
      arguments: args.join(" "),  // Pass all text after command name as arguments
      agent,
      model: `${model.providerID}/${model.modelID}`,
      variant,
      parts: images.map(...),
    })
    return
  }
}
```

### Key Points:
- Command format: `/command-name arguments here`
- Arguments are joined with spaces into a single string
- The entire argument string is passed to the server
- Empty commands (``) are handled gracefully

---

## 2. Skill Registration as Commands

**File**: `packages/opencode/src/command/index.ts` (lines 125-138)

Skills are automatically registered as invokable commands:

```typescript
for (const skill of await Skill.all()) {
  // Skip if a command with this name already exists
  if (result[skill.name]) continue
  result[skill.name] = {
    name: skill.name,
    description: skill.description,
    source: "skill",
    get template() {
      return skill.content  // Skill markdown content is the template
    },
    hints: [],  // Skills don't use numbered hints
  }
}
```

### Key Points:
- Skills are loaded from directories (`.claude/skills/`, `.agents/skills/`, `.opencode/skill/`)
- Skill name is extracted from SKILL.md frontmatter
- Skill content becomes the command template
- Skills have lower priority (skipped if command name already exists)

---

## 3. Command Execution Flow

**File**: `packages/opencode/src/session/prompt.ts` (lines 1744-1886)

### 3.1 Argument Parsing

```typescript
const raw = input.arguments.match(argsRegex) ?? []  // argsRegex = /(?:\[Image\s+\d+\]|"[^"]*"|'[^']*'|[^\s"']+)/gi
const args = raw.map((arg) => arg.replace(quoteTrimRegex, ""))  // Remove surrounding quotes
```

### 3.2 Placeholder Substitution

Commands support numbered placeholders (`$1`, `$2`, etc.) and `$ARGUMENTS`:

```typescript
const placeholders = templateCommand.match(placeholderRegex) ?? []
let last = 0
for (const item of placeholders) {
  const value = Number(item.slice(1))
  if (value > last) last = value
}

// Replace $1, $2, etc. with arguments
const withArgs = templateCommand.replaceAll(placeholderRegex, (_, index) => {
  const position = Number(index)
  const argIndex = position - 1
  if (argIndex >= args.length) return ""
  if (position === last) return args.slice(argIndex).join(" ")  // Final placeholder swallows remaining args
  return args[argIndex]
})

// Replace $ARGUMENTS with the entire argument string
let template = withArgs.replaceAll("$ARGUMENTS", input.arguments)
```

### 3.3 No-Placeholder Handling

If the command template doesn't use placeholders, arguments are appended:

```typescript
if (placeholders.length === 0 && !usesArgumentsPlaceholder && input.arguments.trim()) {
  template = template + "\n\n" + input.arguments
}
```

### 3.4 Shell Command Execution in Templates

Templates can include shell commands with `!`cmd``:

```typescript
const bashRegex = /!`([^`]+)`/g
// ...
const shell = ConfigMarkdown.shell(template)
if (shell.length > 0) {
  const results = await Promise.all(
    shell.map(async ([, cmd]) => {
      try {
        return await $`${{ raw: cmd }}`.quiet().nothrow().text()
      } catch (error) {
        return `Error executing command: ${error instanceof Error ? error.message : String(error)}`
      }
    }),
  )
  let index = 0
  template = template.replace(bashRegex, () => results[index++])
}
```

---

## 4. Agent Triggering

### 4.1 Subtask Mode

**File**: `packages/opencode/src/session/prompt.ts` (lines 1834-1857)

If the agent is a subagent (or command has `subtask: true`), the command creates a subtask part:

```typescript
const isSubtask = (agent.mode === "subagent" && command.subtask !== false) || command.subtask === true
const parts = isSubtask
  ? [
      {
        type: "subtask" as const,
        agent: agent.name,
        description: command.description ?? "",
        command: input.command,
        model: { providerID: taskModel.providerID, modelID: taskModel.modelID },
        prompt: templateParts.find((y) => y.type === "text")?.text ?? "",
      },
    ]
  : [...templateParts, ...(input.parts ?? [])]
```

### 4.2 Regular Mode

If not a subtask, the command triggers a standard agent run:

```typescript
const result = (await prompt({
  sessionID: input.sessionID,
  messageID: input.messageID,
  model: userModel,
  agent: userAgent,
  parts,
  variant: input.variant,
})) as MessageV2.WithParts
```

---

## 5. API Contract

**File**: `packages/sdk/js/src/v2/gen/types.gen.ts` (lines 3752-3804)

```typescript
export type SessionCommandData = {
  body?: {
    messageID?: string
    agent?: string
    model?: string
    arguments: string  // Arguments string (everything after command name)
    command: string    // Command name (without leading /)
    variant?: string
    parts?: Array<...> // Optional file attachments
  }
  path: {
    sessionID: string
  }
}

export type SessionCommandResponses = {
  200: {
    info: AssistantMessage
    parts: Array<Part>
  }
}
```

---

## 6. Event Publishing

**File**: `packages/opencode/src/command/index.ts` (lines 12-21)

When a command is executed, an event is published:

```typescript
export const Event = {
  Executed: BusEvent.define(
    "command.executed",
    z.object({
      name: z.string(),
      sessionID: Identifier.schema("session"),
      arguments: z.string(),
      messageID: Identifier.schema("message"),
    }),
  ),
}
```

This is triggered in `prompt.ts` (lines 1878-1883):

```typescript
Bus.publish(Command.Event.Executed, {
  name: input.command,
  sessionID: input.sessionID,
  arguments: input.arguments,
  messageID: result.info.id,
})
```

---

## 7. Flow Summary

```
User Input: /skill:case-document 介绍下这个 skill
                    ↓
UI Parsing (submit.ts)
  - command: "skill:case-document"
  - arguments: "介绍下这个 skill"
                    ↓
API Call: POST /session/{id}/command
  - command: "skill:case-document"
  - arguments: "介绍下这个 skill"
                    ↓
Command Resolution (command/index.ts)
  - Find skill named "skill:case-document"
  - Get template (skill.content from SKILL.md)
                    ↓
Template Processing (session/prompt.ts)
  - Parse arguments from input string
  - Substitute $1, $2, $ARGUMENTS placeholders
  - If no placeholders: append arguments to template
  - Execute any shell commands (!`cmd`)
                    ↓
Agent Run
  - Create user message with processed template as prompt
  - Trigger agent loop (loop())
  - Stream events back to UI
                    ↓
Event Publishing
  - Publish command.executed event
```

---

## 8. Compatibility Notes for RFC-0017

### 8.1 Argument Handling

1. **Always pass arguments**: Even if command doesn't use them, arguments should be preserved
2. **Argument string format**: Join all args with spaces (preserving quoted strings)
3. **No trimming**: Don't trim the arguments string before passing

### 8.2 Template Processing

1. **Placeholder support**: Support `$1`, `$2`, etc. for positional args
2. **$ARGUMENTS support**: Support `$ARGUMENTS` for entire argument string
3. **Fallback behavior**: If no placeholders, append arguments to template with separator

### 8.3 Skill Content as Template

1. **Raw content**: Use the raw markdown content from SKILL.md as the template
2. **No preprocessing**: Don't preprocess the content before template substitution
3. **Shell execution**: Support shell command substitution with backtick syntax

### 8.4 Agent Run

1. **Standard flow**: After template processing, trigger standard agent run
2. **Subtask support**: Support subtask mode for agents with `mode: "subagent"`
3. **Event streaming**: Stream events back to caller during agent execution

---

## 9. Files Referenced

- `packages/app/src/components/prompt-input/submit.ts` - UI command parsing
- `packages/opencode/src/command/index.ts` - Command registry and skill loading
- `packages/opencode/src/skill/skill.ts` - Skill discovery and loading
- `packages/opencode/src/session/prompt.ts` - Command execution and template processing
- `packages/sdk/js/src/v2/gen/types.gen.ts` - API type definitions
- `packages/app/src/components/prompt-input/build-request-parts.ts` - Request part building

---

## 10. Open Questions

1. **Streaming**: How does OpenCode handle streaming output for skill commands?
   - Answer: Uses the same streaming mechanism as regular prompts via `SessionPrompt.loop()`

2. **Error Handling**: What happens if a skill command fails?
   - Answer: Errors are published via `Session.Event.Error` and shown in UI toast

3. **Permission**: How are skill permissions checked?
   - Answer: Via `PermissionNext.evaluate()` against agent's permission ruleset

4. **Nested Skills**: Can a skill invoke another skill?
   - Answer: Yes, through the standard `skill` tool mechanism
