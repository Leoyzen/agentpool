---
title: User Interaction Toolset
description: Interact with users via forms and questions
icon: material/account-question
---

# User Interaction Toolset

The user interaction toolset provides tools for agents to ask questions and collect structured responses from users. It uses the MCP Elicit protocol so forms can be rendered by compatible clients (IDEs, TUI, etc.).

## Available Tools

| Tool | Purpose |
|---|---|
| `question_for_user` | Present a multi-question XML questionnaire (enum, multi, input types) |
| `ask_followup_question` | Ask a single follow-up question with `<suggest>` tag options |

## `question_for_user`

Takes an XML `questionnaire` string describing one or more questions and presents them as a form.

XML format (use single quotes for attributes to avoid JSON escaping issues):

```xml
<question header="Model" type="enum" required="true">
  <text>What model?</text>
  <suggest type="choice">Option 1</suggest>
  <suggest type="choice">Option 2</suggest>
</question>
```

Supported question types:

- `enum`: single choice from a list of `<suggest>` options
- `multi`: multiple choice
- `input`: free-text input

## `ask_followup_question`

Takes a `question` string and a `follow_up` string containing `<suggest>` tags.

```xml
Do you want to proceed?
<suggest type="choice">Yes</suggest>
<suggest type="choice">No</suggest>
<suggest type="input">Tell me more...</suggest>
```

The user can pick one of the choices or provide custom input.

## Responses

Both tools return the user's answers through the agent tool result. Agents can then use the answers to decide next steps or personalize their response.

For implementation details, see `agentpool_toolsets.builtin.question_tools`.
