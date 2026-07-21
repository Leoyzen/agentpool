# Agent Types

## Native Agents (`type: native`)

- PydanticAI-based agents with full framework features
- Direct model integration (OpenAI, Anthropic, Google, Mistral, etc.)
- Tool support, structured output, streaming
- Most flexible and feature-rich

## ACP Agents (`type: acp`)

- External agents implementing Agent Communication Protocol
- Examples: Goose, custom ACP servers
- Communicate via stdio or websocket

## File Agents (`type: file`)

- Agent behavior defined by file content/prompts
- Lightweight for simple use cases
