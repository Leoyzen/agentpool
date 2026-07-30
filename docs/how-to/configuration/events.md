---
sync:
  agent: doc_sync_agent
  dependencies:
    - src/agentwolf_config/event_handlers.py
title: Event Handlers
description: Event handler setup and configuration
icon: material/bell
---

Event handlers allow you to respond to various agent events such as messages, tool calls, and state changes.

/// mknodes
{{ "agentwolf_config.event_handlers.EventHandlerConfig" | union_to_markdown(display_mode="yaml", header_style="pymdownx") }}
///
