"""Feedback tools for AgentDBCapability.

Phase 5: Provides create_simplified_feedback tool that allows diagnostic
agents (in read mode) to submit feedback as quality tickets. Available in
ALL modes including "read".
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import RunContext  # noqa: TC002 - needed for get_type_hints()
import yaml

from agentpool.capabilities.agent_db.visibility import URIPrefixFilter


if TYPE_CHECKING:
    from collections.abc import Callable

    from agentpool.capabilities.agent_db import AgentDBCapability


def _build_frontmatter(data: dict[str, Any]) -> str:
    """Build YAML frontmatter string from a dict.

    Args:
        data: The frontmatter key-value pairs.

    Returns:
        A YAML frontmatter block delimited by ``---``.
    """
    fm_text = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{fm_text}---\n\n"


def build_feedback_tools(
    cap: AgentDBCapability,
) -> list[Callable[..., Any]]:
    """Build feedback tool functions for AgentDBCapability.

    Returns 1 async tool closure:
    - ``agentdb_create_simplified_feedback`` — submit feedback as QT

    Available in ALL modes (including "read") because diagnostic agents
    need to submit feedback even in read-only mode.

    Args:
        cap: The AgentDBCapability instance that owns these tools.

    Returns:
        A list of 1 async tool function.
    """
    tools: list[Callable[..., Any]] = []
    uri_filter = URIPrefixFilter(allowed_prefixes=cap.allowed_prefixes)

    # Feedback tools available in ALL modes (read, write, all)
    async def agentdb_create_simplified_feedback(
        ctx: RunContext[Any],
        feedback_type: str,
        identity_node: str = "",
        symptom_uri: str = "",
        observation: str = "",
        pattern: str = "",
        cases: list[str] | None = None,
        proposed_scope: str = "",
        entity_uri: str = "",
        contradiction: str = "",
        evidence_uris: list[str] | None = None,
    ) -> ToolReturn:
        """Submit simplified feedback as a quality ticket.

        Based on feedback_type, auto-constructs the appropriate QT:
        - ``"observation"`` → OPS with source="agent_proactive"
        - ``"experience"`` → OPL proposal with proposed_content
        - ``"contradiction"`` → OPA with source="agent_quality_check"

        Args:
            feedback_type: One of "observation", "experience", "contradiction".
            identity_node: Device identity node (optional).
            symptom_uri: Required for observation feedback.
            observation: Observation text (for observation feedback).
            pattern: Experience pattern description (for experience feedback).
            cases: List of case descriptions (for experience feedback).
            proposed_scope: Suggested scope for the experience.
            entity_uri: Entity URI (for contradiction feedback).
            contradiction: Contradiction description (for contradiction feedback).
            evidence_uris: Optional list of evidence URIs.

        Returns:
            JSON with ``feedback_type``, ``qt_uri``, and ``status``.
        """
        import datetime as _dt

        tickets_base = "viking://tickets/"
        if not uri_filter.is_allowed(tickets_base):
            return ToolReturn(
                return_value=(
                    f"Access denied: URI '{tickets_base}' is not in the "
                    f"allowed namespaces for this agent."
                )
            )
        # Validate required fields per feedback_type
        if feedback_type == "observation" and not symptom_uri:
            return ToolReturn(
                return_value=(
                    "ValidationError: symptom_uri is required for feedback_type='observation'."
                )
            )
        if feedback_type == "experience" and not pattern:
            return ToolReturn(
                return_value=(
                    "ValidationError: pattern is required for feedback_type='experience'."
                )
            )
        if feedback_type == "contradiction" and not contradiction:
            return ToolReturn(
                return_value=(
                    "ValidationError: contradiction is required for feedback_type='contradiction'."
                )
            )
        if feedback_type not in ("observation", "experience", "contradiction"):
            return ToolReturn(
                return_value=(
                    f"ValidationError: invalid feedback_type '{feedback_type}'. "
                    f"Must be one of: observation, experience, contradiction."
                )
            )

        try:
            client = await cap.viking._ensure_client()
            ts = _dt.date.today().isoformat()
            qt_id = f"feedback-{ts}-{feedback_type[:3]}"

            if feedback_type == "observation":
                # Create OPS
                qt_uri = f"viking://tickets/ops/{qt_id}.md"
                fm_data: dict[str, Any] = {
                    "type": "ops",
                    "title": f"Observation: {symptom_uri}",
                    "source": "agent_proactive",
                    "ticket_status": "open",
                    "identity_node": identity_node,
                    "symptom_uri": symptom_uri,
                    "observation": observation,
                    "gap_type": "uncovered",  # inferred from observation
                    "created_at": ts,
                }
                if evidence_uris:
                    fm_data["evidence_uris"] = evidence_uris
                content = _build_frontmatter(fm_data)
                content += f"## Observation\n\n{observation}\n\n## Investigation\n\n(Pending)\n"
                await client.write(qt_uri, content)

            elif feedback_type == "experience":
                # Create OPL proposal
                qt_uri = f"viking://tickets/opl_proposal/{qt_id}.md"
                proposed_content: dict[str, Any] = {
                    "pattern": pattern,
                    "cases": cases or [],
                }
                if proposed_scope:
                    proposed_content["proposed_scope"] = proposed_scope
                fm_data = {
                    "type": "opl_proposal",
                    "title": f"Experience: {pattern[:50]}",
                    "proposal_type": "create",
                    "ticket_status": "proposed",
                    "identity_node": identity_node,
                    "proposed_content": proposed_content,
                    "created_at": ts,
                }
                if evidence_uris:
                    fm_data["evidence_uris"] = evidence_uris
                content = _build_frontmatter(fm_data)
                content += f"## Pattern\n\n{pattern}\n\n## Cases\n\n"
                for case in cases or []:
                    content += f"- {case}\n"
                await client.write(qt_uri, content)

            else:  # contradiction
                # Create OPA
                qt_uri = f"viking://tickets/opa/{qt_id}.md"
                fm_data = {
                    "type": "opa",
                    "title": f"Contradiction: {entity_uri}",
                    "source": "agent_quality_check",
                    "ticket_status": "open",
                    "identity_node": identity_node,
                    "entity_uri": entity_uri,
                    "description": contradiction,
                    "created_at": ts,
                }
                if evidence_uris:
                    fm_data["evidence_uris"] = evidence_uris
                content = _build_frontmatter(fm_data)
                content += f"## Contradiction\n\n{contradiction}\n"
                await client.write(qt_uri, content)

            result = {
                "feedback_type": feedback_type,
                "qt_uri": qt_uri,
                "status": "created",
            }
            return ToolReturn(return_value=json.dumps(result, ensure_ascii=False, default=str))
        except Exception as e:
            return ToolReturn(return_value=f"Error: {e}")

    tools.append(agentdb_create_simplified_feedback)

    return tools
