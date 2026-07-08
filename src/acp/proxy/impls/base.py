"""Proxy type registry for mapping string discriminators to proxy classes."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from acp.proxy.protocol import Proxy


class ProxyRegistry:
    """Registry mapping string type discriminators to proxy classes."""

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._registry: dict[str, type[Proxy]] = {}

    def register(self, type_name: str, proxy_class: type[Proxy]) -> None:
        """Register a proxy class under a type discriminator.

        Args:
            type_name: The string discriminator (e.g., "hook", "context_injection").
            proxy_class: The class implementing the Proxy protocol.

        Raises:
            ValueError: If the type_name is already registered.
        """
        if type_name in self._registry:
            msg = f"Proxy type '{type_name}' is already registered"
            raise ValueError(msg)
        self._registry[type_name] = proxy_class

    def get(self, type_name: str) -> type[Proxy]:
        """Retrieve a proxy class by type discriminator.

        Args:
            type_name: The string discriminator to look up.

        Returns:
            The registered proxy class.

        Raises:
            KeyError: If the type_name is not registered.
        """
        if type_name not in self._registry:
            msg = (
                f"Unknown proxy type: '{type_name}'. "
                f"Registered types: {self.registered_types()}"
            )
            raise KeyError(msg)
        return self._registry[type_name]

    def is_registered(self, type_name: str) -> bool:
        """Check if a type discriminator is registered."""
        return type_name in self._registry

    def registered_types(self) -> list[str]:
        """Return a sorted list of all registered type names."""
        return sorted(self._registry.keys())

    def __len__(self) -> int:
        """Return the number of registered types."""
        return len(self._registry)

    def __contains__(self, type_name: object) -> bool:
        """Check if a type name is registered."""
        if isinstance(type_name, str):
            return type_name in self._registry
        return False


default_registry = ProxyRegistry()
