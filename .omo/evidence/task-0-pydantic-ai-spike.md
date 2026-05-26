# pydantic-ai Provider Injection Pre-Research Spike

## Summary

**Verdict: GO** — ProviderRouter can be implemented at the ACP protocol layer without modifying pydantic-ai internals. Runtime provider override for active sessions is deferred per RFC guardrails.

## Provider Initialization in pydantic-ai

### Provider Class
Located at `pydantic_ai_slim/pydantic_ai/providers/__init__.py`:
- `Provider` is an abstract base class (ABC, Generic[InterfaceClient])
- Key properties: `name: str`, `base_url: str`, `client: InterfaceClient`
- Lifecycle: `__aenter__` / `__aexit__` for HTTP client management
- Each provider manages its own authenticated HTTP client

### Model-Provider Binding
In `pydantic_ai/models/openai.py`:
- `OpenAIChatModel` has `_provider: Provider[AsyncOpenAI]` field
- Provider is passed via `__init__` or inferred from model name
- Models use `provider.client` to make API calls

### Injection Points
1. **Model initialization**: Override `provider` parameter when creating model instances
2. **Agent level**: `Agent` accepts `model` parameter — can pass a model with custom provider
3. **Provider override**: Subclass existing provider and override `base_url` / `client`

### Recommendation for RFC-0034
- **Phase 0-2**: ProviderRouter tracks metadata only (id, name, protocol, base_url, status)
- **Future**: For runtime override, create custom Provider subclass or use model factory with overridden base_url
- **No pydantic-ai modifications needed** — override happens at AgentPool model creation level

## Code References
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/providers/__init__.py:25` — Provider ABC
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/models/openai.py:756` — _provider field
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/agent.py` — Agent model binding

## Conclusion
ProviderRouter in RFC-0034 operates at the ACP protocol/metadata layer. Actual model provider override (base_url, api_key) would require per-session agent recreation with modified model config, which is out of scope for this RFC per guardrails. The GO verdict confirms we can proceed with metadata-only ProviderRouter implementation.
