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

# State machine: allowed transitions
_transitions: dict[str, frozenset[str]] = {
    "open": frozenset({"reviewing", "rejected"}),
    "reviewing": frozenset({"approved", "rejected", "reworked"}),
    "approved": frozenset({"materialized"}),
    "reworked": frozenset({"reviewing", "rejected"}),
    "rejected": frozenset(),
    "materialized": frozenset(),
}
_action_to_status: dict[str, str] = {
    "submit_for_review": "reviewing",
    "approve": "approved",
    "reject": "rejected",
    "rework": "reworked",
    "materialize": "materialized",
}


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

        # ---- 3. agentdb_create_qt ----
        async def agentdb_create_qt(
            ctx: RunContext[Any],
            qt_type: str,
            qt_data: dict[str, Any],
            qt_id: str,
            parent_qt: str = "",
        ) -> ToolReturn:
            """Create a quality ticket (QT) file.

            Validates the QT type, constructs frontmatter from qt_data
            with initial ``ticket_status=open``, and writes the file
            to ``viking://tickets/{qt_type}/{qt_id}.md``.

            Args:
                qt_type: One of ``"opa"``, ``"ops"``, ``"opl_proposal"``.
                qt_data: Dict with QT fields (title, description, etc.).
                qt_id: Unique ID for the QT file.
                parent_qt: Optional parent QT URI for sub-QTs.

            Returns:
                JSON with ``qt_uri`` and ``status``.
            """
            if qt_type not in _VALID_QT_TYPES:
                valid_types = ", ".join(sorted(_VALID_QT_TYPES))
                return ToolReturn(
                    return_value=(
                        f"ValidationError: invalid qt_type '{qt_type}'. "
                        f"Must be one of: {valid_types}"
                    )
                )
            qt_uri = f"viking://tickets/{qt_type}/{qt_id}.md"
            if not uri_filter.is_allowed(qt_uri):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{qt_uri}' is not in the "
                        f"allowed namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                fm_data: dict[str, Any] = {
                    "type": qt_type,
                    "ticket_status": "open",
                }
                fm_data.update(qt_data)
                if parent_qt:
                    fm_data["parent_qt"] = parent_qt
                content = _build_frontmatter(fm_data)
                body = qt_data.get("body", "")
                if body:
                    content += body
                await client.write(qt_uri, content)
                result = {
                    "qt_uri": qt_uri,
                    "status": "created",
                    "qt_type": qt_type,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False, default=str))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_create_qt)

        # ---- 4. agentdb_create_sub_qt ----
        async def agentdb_create_sub_qt(
            ctx: RunContext[Any],
            parent_qt: str,
            qt_type: str,
            qt_data: dict[str, Any],
            qt_id: str,
        ) -> ToolReturn:
            """Create a child QT linked to a parent QT.

            Validates that the parent QT exists, then creates a new
            QT with the ``parent_qt`` field set.

            Args:
                parent_qt: URI of the parent QT.
                qt_type: One of ``"opa"``, ``"ops"``, ``"opl_proposal"``.
                qt_data: Dict with QT fields.
                qt_id: Unique ID for the child QT.

            Returns:
                JSON with ``qt_uri`` and ``status``.
            """
            if not uri_filter.is_allowed(parent_qt):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{parent_qt}' is not in the "
                        f"allowed namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                # Verify parent exists
                parent_content = await client.read(parent_qt)
                if not parent_content:
                    return ToolReturn(return_value=f"Parent QT not found at {parent_qt}")
                # Create child QT with parent_qt field
                qt_uri = f"viking://tickets/{qt_type}/{qt_id}.md"
                if not uri_filter.is_allowed(qt_uri):
                    return ToolReturn(
                        return_value=(
                            f"Access denied: URI '{qt_uri}' is not in the "
                            f"allowed namespaces for this agent."
                        )
                    )
                fm_data: dict[str, Any] = {
                    "type": qt_type,
                    "ticket_status": "open",
                    "parent_qt": parent_qt,
                }
                fm_data.update(qt_data)
                content = _build_frontmatter(fm_data)
                body = qt_data.get("body", "")
                if body:
                    content += body
                await client.write(qt_uri, content)
                result = {
                    "qt_uri": qt_uri,
                    "status": "created",
                    "parent_qt": parent_qt,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False, default=str))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_create_sub_qt)

        # ---- 5. agentdb_transition_qt ----

        async def agentdb_transition_qt(
            ctx: RunContext[Any],
            qt_uri: str,
            action: str,
            comment: str = "",
            cr_action: str = "",
        ) -> ToolReturn:
            """Transition a QT to a new status.

            Validates the state transition is allowed per the state
            machine, updates ``ticket_status``, and optionally appends
            a CR (change record) to the body.

            Args:
                qt_uri: URI of the QT file.
                action: Transition action (submit_for_review, approve,
                    reject, rework, materialize).
                comment: Optional comment for the CR record.
                cr_action: Optional CR action label.

            Returns:
                JSON with ``old_status``, ``new_status``, and ``qt_uri``.
            """
            if not uri_filter.is_allowed(qt_uri):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{qt_uri}' is not in the "
                        f"allowed namespaces for this agent."
                    )
                )
            new_status = _action_to_status.get(action)
            if new_status is None:
                return ToolReturn(
                    return_value=(
                        f"InvalidTransition: unknown action '{action}'. "
                        f"Valid actions: {', '.join(sorted(_action_to_status))}"
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                content = await client.read(qt_uri)
                if not content:
                    return ToolReturn(return_value=f"QT not found at {qt_uri}")
                fm, body = parse_frontmatter(content)
                old_status = str(fm.get("ticket_status", ""))
                # Validate transition
                allowed = _transitions.get(old_status, frozenset())
                if new_status not in allowed:
                    return ToolReturn(
                        return_value=(
                            f"InvalidTransition: cannot transition from "
                            f"'{old_status}' to '{new_status}' via '{action}'."
                        )
                    )
                # Update frontmatter
                fm["ticket_status"] = new_status
                # Append CR record to body if requested
                new_body = body
                if cr_action or comment:
                    import datetime as _dt

                    ts = _dt.date.today().isoformat()
                    cr_parts = [f"cr: {ts}"]
                    if cr_action:
                        cr_parts.append(f"action: {cr_action}")
                    if comment:
                        cr_parts.append(f"comment: {comment}")
                    cr_line = f"<!-- {' | '.join(cr_parts)} -->\n"
                    new_body = body + "\n" + cr_line if body else cr_line
                new_content = _build_frontmatter(fm) + new_body
                await client.write(qt_uri, new_content)
                result = {
                    "qt_uri": qt_uri,
                    "old_status": old_status,
                    "new_status": new_status,
                    "action": action,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False, default=str))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_transition_qt)

    return tools
