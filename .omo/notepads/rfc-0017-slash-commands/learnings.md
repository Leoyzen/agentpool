# OpenCode Slash Command Handling - RFC-0017 Research

## Summary

When a user types `/skill:case-document 介绍下这个 skill`, OpenCode handles it through a specific flow.

## 1. Frontend Command Detection & Submission

**File**: `packages/app/src/components/prompt-input.tsx`

```typescript
const slashMatch = rawText.match(/^\/(\S*)$/)  // Detects slash commands
if (slashMatch) {
  slashOnInput(slashMatch[1])  // Triggers slash command popover
  setStore("popover", "slash")
}
```

**File**: `packages/app/src/components/prompt-input/submit.ts`

```typescript
if (text.startsWith("/")) {
  const [cmdName, ...args] = text.split(" ")
  const commandName = cmdName.slice(1)
  const customCommand = sync.data.command.find((c) => c.name === commandName)
  if (customCommand) {
    client.session.command({
      sessionID: session.id,
      command: commandName,        // "skill:case-document"
      arguments: args.join(" "),   // "介绍下这个 skill"
      agent,
      model: `${model.providerID}/${model.modelID}`,
      ...
    })
  }
}
```

**Key Point**: Arguments are passed as a single string via the `arguments` field.

---

## 2. How Skills Become Commands

**File**: `packages/opencode/src/command/index.ts`

```typescript
// Add skills as invokable commands
for (const skill of await Skill.all()) {
  if (result[skill.name]) continue
  result[skill.name] = {
    name: skill.name,
    description: skill.description,
    source: "skill",
    get template() {
      return skill.content  // Skill content becomes command template
    },
    hints: [],
  }
}
```

Skills are loaded from:
- `.opencode/skills/**/SKILL.md`
- `.claude/skills/**/SKILL.md`
- `.agents/skills/**/SKILL.md`
- Custom paths from config
- URLs for remote skills

---

## 3. Command Argument Parsing

**File**: `packages/opencode/src/session/prompt.ts`

```typescript
export async function command(input: CommandInput) {
  const command = await Command.get(input.command)
  const raw = input.arguments.match(argsRegex) ?? []
  const args = raw.map((arg) => arg.replace(quoteTrimRegex, ""))

  const templateCommand = await command.template

  // Parse $1, $2, etc. placeholders
  const placeholders = templateCommand.match(placeholderRegex) ?? []
  let last = 0
  for (const item of placeholders) {
    const value = Number(item.slice(1))
    if (value > last) last = value
  }

  // Replace placeholders with arguments
  const withArgs = templateCommand.replaceAll(placeholderRegex, (_, index) => {
    const position = Number(index)
    const argIndex = position - 1
    if (argIndex >= args.length) return ""
    if (position === last) return args.slice(argIndex).join(" ")  // Last gets remaining
    return args[argIndex]
  })
  
  // Handle $ARGUMENTS placeholder
  let template = withArgs.replaceAll("$ARGUMENTS", input.arguments)
  
  // If no placeholders, append arguments to template
  if (placeholders.length === 0 && !usesArgumentsPlaceholder && input.arguments.trim()) {
    template = template + "\n\n" + input.arguments
  }
}
```

**Key Points**:
- Supports `$1`, `$2`, etc. for positional arguments
- `$ARGUMENTS` gets the entire arguments string
- If no placeholders, arguments are appended to the template with `\n\n`
- Supports quoted arguments (parsed by `argsRegex`)

---

## 4. Agent Triggering After Commands

**File**: `packages/opencode/src/session/prompt.ts`

```typescript
export async function command(input: CommandInput) {
  // Determine which agent to use
  const agentName = command.agent ?? input.agent ?? (await Agent.defaultAgent())
  const agent = await Agent.get(agentName)
  
  // Determine if this should run as a subtask
  const isSubtask = (agent.mode === "subagent" && command.subtask !== false) 
                    || command.subtask === true
  
  const parts = isSubtask
    ? [
        {
          type: "subtask",
          agent: agent.name,
          description: command.description ?? "",
          command: input.command,
          model: { providerID: taskModel.providerID, modelID: taskModel.modelID },
          prompt: templateParts.find((y) => y.type === "text")?.text ?? "",
        },
      ]
    : [...templateParts, ...(input.parts ?? [])]

  // Trigger the prompt - THIS STARTS THE AGENT
  const result = (await prompt({
    sessionID: input.sessionID,
    messageID: input.messageID,
    model: userModel,
    agent: userAgent,
    parts,
    variant: input.variant,
  })) as MessageV2.WithParts

  // Publish event
  Bus.publish(Command.Event.Executed, {
    name: input.command,
    sessionID: input.sessionID,
    arguments: input.arguments,
    messageID: result.info.id,
  })
}
```

**Key Point**: YES, commands trigger agent runs via the `prompt()` function.

---

## 5. Prompt Formatting for Agent

After argument replacement:
1. Command template loaded (skill content or command definition)
2. Placeholders (`$1`, `$2`, `$ARGUMENTS`) replaced with user arguments
3. Shell commands (`` !`command` ``) are executed and output injected
4. Final template becomes the prompt text sent to the agent

**Example Flow**:
- User input: `/skill:case-document 介绍下这个 skill`
- Skill template: `Document this code: $ARGUMENTS`
- After replacement: `Document this code: 介绍下这个 skill`
- Agent receives this as the user prompt

---

## 6. UI Display

**File**: `packages/app/src/components/prompt-input/slash-popover.tsx`

```typescript
<For each={props.slashFlat}>
  {(cmd) => (
    <button data-slash-id={cmd.id} onClick={() => props.onSlashSelect(cmd)}>
      <span>/{cmd.trigger}</span>
      <span>{cmd.description}</span>
      <Show when={cmd.type === "custom" && cmd.source !== "command"}>
        <span>
          {cmd.source === "skill" ? "skill" 
           : cmd.source === "mcp" ? "mcp"
           : "custom"}
        </span>
      </Show>
    </button>
  )}
</For>
```

Skills are shown with a "skill" badge in the slash command popover.

---

## 7. Event/Streaming Handling

**File**: `packages/opencode/src/command/index.ts`

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

**File**: `packages/opencode/src/server/routes/session.ts`

```typescript
.post("/:sessionID/command", async (c) => {
  const sessionID = c.req.valid("param").sessionID
  const body = c.req.valid("json")
  const msg = await SessionPrompt.command({ ...body, sessionID })
  return c.json(msg)  // Returns full message response (not streaming)
})
```

For streaming, the system uses `SessionPrompt.loop()` which publishes events via the Bus system.

---

## Flow Summary

```
User types: /skill:case-document 介绍下这个 skill
    ↓
Frontend detects slash command via regex
    ↓
Splits: commandName="skill:case-document", arguments="介绍下这个 skill"
    ↓
Calls client.session.command() API
    ↓
Backend loads skill content as template
    ↓
Replaces placeholders ($1, $2, $ARGUMENTS) with arguments
    ↓
Calls prompt() → triggers agent execution
    ↓
Agent processes formatted prompt
    ↓
Response streamed via Bus events to UI
```

## Compatibility Notes for RFC-0017

1. **Arguments are passed as a single string** via the `arguments` field
2. **Agent is always triggered** after command processing
3. **Template substitution** supports `$1`, `$2`, `$ARGUMENTS`
4. **If no placeholders**, arguments appended with `\n\n` separator
5. **Skills loaded dynamically** from `SKILL.md` files
6. **Event published** on command execution: `Command.Event.Executed`
