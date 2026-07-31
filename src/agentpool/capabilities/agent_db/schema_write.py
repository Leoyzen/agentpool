"""Schema-aware write tools for AgentDBCapability.

Phase 4: Tools that create and modify knowledge entities through the
OPL (One Point Lesson) proposal workflow. Only available in write/all mode.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import RunContext  # noqa: TC002 - needed for get_type_hints()
import yaml

from agentpool.capabilities.agent_db.helpers import parse_frontmatter
from agentpool.capabilities.agent_db.visibility import URIPrefixFilter


if TYPE_CHECKING:
    from collections.abc import Callable

    from agentpool.capabilities.agent_db import AgentDBCapability


_VALID_ENTITY_TYPES = frozenset({
    "component",
    "component_class",
    "fault",
    "symptom",
    "opl",
    "domain",
    "procedure",
})

_VALID_QT_TYPES = frozenset({"opa", "ops", "opl_proposal"})


def _build_frontmatter(data: dict[str, Any]) -> str:
    """Build YAML frontmatter string from a dict.

    Args:
        data: The frontmatter key-value pairs.

    Returns:
        A YAML frontmatter block delimited by ``---``.
    """
    fm_text = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{fm_text}---\n\n"


def build_schema_write_tools(
    cap: AgentDBCapability,
) -> list[Callable[..., Any]]:
    """Build schema-aware write tool functions for AgentDBCapability.

    Returns 2 async tool closures (create_entity, update_entity).
    Only available when ``cap.mode`` is ``"write"`` or ``"all"``.

    Args:
        cap: The AgentDBCapability instance that owns these tools.

    Returns:
        A list of async tool functions (empty in read mode).
    """
    tools: list[Callable[..., Any]] = []
    uri_filter = URIPrefixFilter(allowed_prefixes=cap.allowed_prefixes)

    if cap.mode in ("write", "all"):
        # ---- 1. agentdb_create_entity ----
        async def agentdb_create_entity(
            ctx: RunContext[Any],
            entity_type: str,
            entity_data: dict[str, Any],
            opl_proposal_id: str,
        ) -> ToolReturn:
            """Create an OPL proposal for a new knowledge entity.

            Validates the entity type, constructs an OPL proposal
            with ``proposal_type=create``, and writes it to
            ``viking://tickets/opl_proposal/{opl_proposal_id}.md``.

            Args:
                entity_type: One of 7 entity types (component, component_class,
                    fault, symptom, opl, domain, procedure).
                entity_data: Dict with entity fields (title, description, etc.).
                opl_proposal_id: Unique ID for the OPL proposal.

            Returns:
                JSON with ``opl_proposal_uri`` and ``status``.
            """
            if entity_type not in _VALID_ENTITY_TYPES:
                valid_types = ", ".join(sorted(_VALID_ENTITY_TYPES))
                return ToolReturn(
                    return_value=(
                        f"ValidationError: invalid entity_type '{entity_type}'. "
                        f"Must be one of: {valid_types}"
                    )
                )
            proposal_uri = f"viking://tickets/opl_proposal/{opl_proposal_id}.md"
            if not uri_filter.is_allowed(proposal_uri):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{proposal_uri}' is not in the "
                        f"allowed namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                # Construct OPL proposal frontmatter
                proposal_fm: dict[str, Any] = {
                    "type": "opl_proposal",
                    "title": f"Create {entity_type}: {entity_data.get('title', '')}",
                    "proposal_type": "create",
                    "target_entity": entity_data.get("title", ""),
                    "entity_type": entity_type,
                    "ticket_status": "proposed",
                    "proposed_content": entity_data,
                }
                proposal_content = _build_frontmatter(proposal_fm)
                await client.write(proposal_uri, proposal_content)
                result = {
                    "opl_proposal_uri": proposal_uri,
                    "status": "created",
                    "proposal_type": "create",
                    "entity_type": entity_type,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False, default=str))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_create_entity)

        # ---- 2. agentdb_update_entity ----
        async def agentdb_update_entity(
            ctx: RunContext[Any],
            entity_uri: str,
            changes: dict[str, Any],
            opl_proposal_id: str,
        ) -> ToolReturn:
            """Create an OPL proposal to modify an existing entity.

            Reads the existing entity to capture its current version
            (base_version), then constructs an OPL proposal with
            ``proposal_type=modify`` and writes it.

            Args:
                entity_uri: URI of the existing entity to modify.
                changes: Dict of field changes to apply.
                opl_proposal_id: Unique ID for the OPL proposal.

            Returns:
                JSON with ``opl_proposal_uri``, ``status``, and ``base_version``.
            """
            if not uri_filter.is_allowed(entity_uri):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{entity_uri}' is not in the "
                        f"allowed namespaces for this agent."
                    )
                )
            proposal_uri = f"viking://tickets/opl_proposal/{opl_proposal_id}.md"
            if not uri_filter.is_allowed(proposal_uri):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{proposal_uri}' is not in the "
                        f"allowed namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                # Read existing entity to get base_version
                existing_content = await client.read(entity_uri)
                if not existing_content:
                    return ToolReturn(return_value=f"Entity not found at {entity_uri}")
                fm, _ = parse_frontmatter(existing_content)
                base_version = fm.get("version", 1)
                # Construct OPL proposal
                proposal_fm: dict[str, Any] = {
                    "type": "opl_proposal",
                    "title": f"Modify: {entity_uri}",
                    "proposal_type": "modify",
                    "target_entity": entity_uri,
                    "base_version": base_version,
                    "ticket_status": "proposed",
                    "proposed_content": changes,
                }
                proposal_content = _build_frontmatter(proposal_fm)
                await client.write(proposal_uri, proposal_content)
                result = {
                    "opl_proposal_uri": proposal_uri,
                    "status": "created",
                    "proposal_type": "modify",
                    "base_version": base_version,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False, default=str))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_update_entity)

    return tools
