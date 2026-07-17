"""Proxy chain configuration models."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class BaseProxyConfig(BaseModel):
    """Base configuration for proxy chain entries."""

    type: str = Field(..., description="Proxy type discriminator")

    @model_validator(mode="after")
    def validate_proxy_type(self) -> BaseProxyConfig:
        """Validate that the proxy type is a known type.

        Currently rejects all types since no concrete proxy types exist yet.
        T17 will convert this to a proper discriminated union.
        """
        known_types: frozenset[str] = frozenset({"hook", "context_injection", "tool_provider"})
        if self.type not in known_types:
            msg = f"Unknown proxy type: {self.type}"
            raise ValueError(msg)
        return self


# T17 will convert this to Annotated[Union[...], Field(discriminator="type")]
ProxyChainConfig = BaseProxyConfig
