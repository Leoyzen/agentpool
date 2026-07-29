"""Test ResourceAccess capability for integration tests.

A minimal ``AbstractCapability`` that implements the ``ResourceAccess``
protocol. Used by resource resolution tests to verify that config-defined
capabilities are discoverable via ``_resolve_resource()``.

Registered in agent configs via ``GenericCapabilityConfig``:

```yaml
capabilities:
  - type: tests.fixtures.test_resource_cap.TestResourceAccessCap
    args:
      read_text: "hello world"
      read_uri: "test://doc.md"
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability

from agentpool.capabilities.resource_protocols import (
    ResourceEntry,
    TextResourceContent,
)


if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass
class TestResourceAccessCap(AbstractCapability[Any]):
    """Minimal capability implementing ``ResourceAccess`` for tests.

    Returns a fixed text for ``read_resource()`` when the URI matches
    ``read_uri``. Returns ``None`` otherwise.
    """

    read_text: str = "hello world"
    read_uri: str = "test://doc.md"
    _owns_client: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        pass

    async def list_resources(self) -> Sequence[ResourceEntry]:
        return [
            ResourceEntry(
                uri=self.read_uri,
                name="doc.md",
                description="Test resource",
                mime_type="text/markdown",
            )
        ]

    async def read_resource(self, uri: str) -> list[TextResourceContent] | None:
        if uri == self.read_uri:
            return [TextResourceContent(text=self.read_text, uri=uri)]
        return None

    async def resource_exists(self, uri: str) -> bool:
        return uri == self.read_uri
