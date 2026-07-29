---
title: AI Models
description: Language model setup and configuration
icon: material/brain
---

## Overview

AgentPool supports a wide range of model types thanks to `Pydantic-AI`. In the simplest form, models are defined by their "identifier", which is defined as `PROVIDER_NAME:MODEL_NAME` (example: `"openai:gpt-5-nano"`).

For more advanced scenarios, it is also possible to assign a more detailed model config including model settings like `temperature` etc.

In addition, some more experimental (meta-)Models are supported using [LLMling-models](https://github.com/phil65/LLMling-models).

These include models which let the user get into the role of an Agent, as well as fallback models and lot more.

```yaml
agents:
  my_agent:
    model: openai:gpt-5-nano  # simple model identifier
  my_agent2:
    model:  # extended model config
      provider: openai
      model: gpt-5-nano
      temperature: 0.5
```

## Supported Models

AgentPool supports the following model providers through Pydantic-AI:

- **openai**: OpenAI models (GPT-4, GPT-3.5, etc.)
- **anthropic**: Anthropic Claude models
- **google-vertex**: Google Vertex AI models
- **groq**: Groq models
- **mistral**: Mistral AI models
- **cohere**: Cohere models
- **gemini**: Google Gemini models
- **ollama**: Local models via Ollama

## Model Configuration Options

Models can be configured with:

| Setting | Description | Default |
|---------|-------------|---------|
| `provider` | Model provider name | Required |
| `model` | Model identifier | Required |
| `temperature` | Sampling temperature | 0.7 |
| `max_tokens` | Maximum tokens per response | Varies |
| `top_p` | Top-p sampling | 1.0 |
| `timeout` | Request timeout | 60s |

For the full schema documentation, see the [LLMling-models](https://github.com/phil65/LLMling-models) package.