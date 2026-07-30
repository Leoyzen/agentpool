# Import Patterns

```python
# Avoid circular imports - use TYPE_CHECKING
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentwolf.delegation import AgentPool

# Config models are in agentwolf_config to avoid circular deps
from agentwolf_config.teams import TeamConfig
```
