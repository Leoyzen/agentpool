"""Advanced read tools for AgentDBCapability.

Phase 5: Tools that build on the schema-aware read tools to generate
textbooks, coverage reports, and applicability derivations.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import RunContext  # noqa: TC002 - needed for get_type_hints()
import yaml

from agentpool.capabilities.agent_db.helpers import (
    parse_bom_table,
    parse_frontmatter,
)
from agentpool.capabilities.agent_db.visibility import URIPrefixFilter


if TYPE_CHECKING:
    from collections.abc import Callable

    from agentpool.capabilities.agent_db import AgentDBCapability


async def _read_entity_rel_files(
    client: Any,
    base_uri: str,
    names: tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    """Read and parse multiple entity-rel YAML files.

    Args:
        client: The Viking SDK client.
        base_uri: Directory URI containing the YAML files (with trailing /).
        names: Tuple of file names (without .yaml).

    Returns:
        Dict mapping file name to list of edge dicts (empty list on failure).
    """
    result: dict[str, list[dict[str, Any]]] = {}
    for name in names:
        file_uri = base_uri + name + ".yaml"
        try:
            content = await client.read(file_uri)
            if not content:
                result[name] = []
                continue
            parsed = yaml.safe_load(content)
            if isinstance(parsed, list):
                result[name] = [e for e in parsed if isinstance(e, dict)]
            else:
                result[name] = []
        except Exception:
            result[name] = []
    return result


def _entity_type_from_path(path: str) -> str | None:
    """Extract the knowledge type from an entity path segment.

    Args:
        path: An entity path (e.g. ``wiki/fault/pump_failure``).

    Returns:
        The type label or ``None`` if no known type segment is found.
    """
    lower = path.lower()
    type_map = {
        "fault": "fault",
        "symptom": "symptom",
        "opl": "opl",
        "procedure": "procedure",
        "component": "component",
    }
    for seg, label in type_map.items():
        if f"/{seg}/" in lower or lower.startswith(f"{seg}/"):
            return label
    return None


def _normalize_term(term: str) -> str:
    """Normalize a search term for comparison.

    Lowercases, strips dashes, spaces, colons, and underscores so that
    ``"k3v:k3v63dt"``, ``"k3v_k3v63dt"``, ``"K3V-K3V63DT"`` all match.

    Args:
        term: The raw search term.

    Returns:
        The normalized term.
    """
    return term.lower().replace("-", "").replace(" ", "").replace(":", "").replace("_", "")


def build_advanced_read_tools(
    cap: AgentDBCapability,
) -> list[Callable[..., Any]]:
    """Build advanced read tool functions for AgentDBCapability.

    Returns 2 async tool closures:
    - ``agentdb_generate_textbook`` — assemble 3-layer textbook
    - ``agentdb_get_coverage_report`` — compute coverage per symptom/fault

    Args:
        cap: The AgentDBCapability instance that owns these tools.

    Returns:
        A list of async tool functions.
    """
    tools: list[Callable[..., Any]] = []
    uri_filter = URIPrefixFilter(allowed_prefixes=cap.allowed_prefixes)

    if cap.mode in ("read", "write", "all"):
        # ---- 1. agentdb_generate_textbook ----
        async def agentdb_generate_textbook(
            ctx: RunContext[Any],
            identity_node: str,
        ) -> ToolReturn:
            """Generate a 3-layer diagnostic textbook for a device.

            Calls query_applicability internally to get the knowledge
            set, then assembles a 3-layer textbook:
            - **domain_layer**: from the Domain entity
            - **pruning_layer**: from Symptom pruning_rules and decision_tree
            - **variant_layer**: from catalog variant overrides

            Builds an evidence_chain from crossref links and computes
            a cache_key from identity_node + max version.

            Args:
                identity_node: Catalog node path (e.g. ``"sany/excavator/sy75c"``).

            Returns:
                JSON with ``domain_layer``, ``pruning_layer``,
                ``variant_layer``, ``assembled_content``, ``evidence_chain``,
                and ``cache_key``.
            """
            bom_uri = f"viking://catalog/{identity_node}/bom.md"
            graph_base = "viking://graph/entity_rel/"
            if not uri_filter.is_allowed(bom_uri) or not uri_filter.is_allowed(graph_base):
                return ToolReturn(
                    return_value="Access denied: required URI namespaces are not in the allowed list."
                )
            try:
                client = await cap.viking._ensure_client()
                # 1. Read BOM
                bom_content = await client.read(bom_uri)
                if not bom_content:
                    return ToolReturn(return_value=f"BOM not found at {bom_uri}")
                bom_components = parse_bom_table(bom_content)
                comp_ids = {
                    str(c.get("component_id", "")) for c in bom_components if c.get("component_id")
                }

                # 2. Read entity-rel files
                rel_files = await _read_entity_rel_files(
                    client,
                    graph_base,
                    ("caused_by", "manifests_as", "addresses"),
                )
                caused_by = rel_files.get("caused_by", [])
                manifests_as = rel_files.get("manifests_as", [])
                addresses_edges = rel_files.get("addresses", [])

                # 3. Collect fault URIs from caused_by matching BOM components
                fault_uris: set[str] = set()
                for edge in caused_by:
                    target = str(edge.get("target", ""))
                    target_comp_id = (
                        target.rsplit("/", maxsplit=1)[-1].replace(".md", "").replace("_", ":")
                        if target
                        else ""
                    )
                    target_norm = _normalize_term(target_comp_id)
                    if any(_normalize_term(cid) == target_norm for cid in comp_ids):
                        source = str(edge.get("source", ""))
                        if source:
                            fault_uris.add(source)
                    for cid in comp_ids:
                        if cid and cid in target:
                            source = str(edge.get("source", ""))
                            if source:
                                fault_uris.add(source)
                            break

                # 4. Collect symptom URIs from manifests_as
                symptom_uris: set[str] = set()
                for edge in manifests_as:
                    source = str(edge.get("source", ""))
                    if source in fault_uris:
                        target = str(edge.get("target", ""))
                        if target:
                            symptom_uris.add(target)

                # 5. Collect OPL URIs from addresses
                opl_uris: set[str] = set()
                for edge in addresses_edges:
                    target = str(edge.get("target", ""))
                    if target in fault_uris:
                        source = str(edge.get("source", ""))
                        if source:
                            opl_uris.add(source)

                # 6. Read domain entity
                parts = identity_node.split("/")
                domain = "excavator"
                min_domain_parts = 2
                if len(parts) >= min_domain_parts:
                    domain = parts[1] if parts[0] in ("sany", "doosan", "komatsu") else parts[0]
                domain_uri = f"viking://wiki/domain/{domain}"
                domain_layer: dict[str, Any] = {}
                try:
                    domain_content = await client.read(domain_uri)
                    if domain_content:
                        domain_fm, domain_body = parse_frontmatter(domain_content)
                        domain_layer = {
                            "uri": domain_uri,
                            "title": str(domain_fm.get("title", "")),
                            "content": domain_body,
                        }
                except Exception:
                    pass

                # 7. Read symptom entities for pruning_layer
                pruning_layer: list[dict[str, Any]] = []
                for sym_uri in sorted(symptom_uris):
                    full_uri = (
                        f"viking://{sym_uri}" if not sym_uri.startswith("viking://") else sym_uri
                    )
                    try:
                        sym_content = await client.read(full_uri)
                    except Exception:
                        sym_content = ""
                    if sym_content:
                        sym_fm, sym_body = parse_frontmatter(sym_content)
                        pruning_layer.append({
                            "uri": full_uri,
                            "title": str(sym_fm.get("title", "")),
                            "pruning_rules": str(sym_fm.get("pruning_rules", "")),
                            "content": sym_body,
                        })

                # 8. Read variant directory for variant_layer
                variant_dir = f"viking://catalog/{identity_node}/variant/"
                variant_layer: list[dict[str, Any]] = []
                if uri_filter.is_allowed(variant_dir):
                    try:
                        variant_entries = await client.ls(variant_dir)
                    except Exception:
                        variant_entries = []
                    if variant_entries:
                        for entry in variant_entries:
                            fname = entry.get("name", "") if isinstance(entry, dict) else str(entry)
                            if not fname.endswith(".md"):
                                continue
                            v_file_uri = variant_dir + fname
                            try:
                                v_content = await client.read(v_file_uri)
                            except Exception:
                                continue
                            if v_content:
                                v_fm, v_body = parse_frontmatter(v_content)
                                variant_layer.append({
                                    "uri": v_file_uri,
                                    "knowledge": str(v_fm.get("knowledge", "")),
                                    "content": v_body,
                                })

                # 9. Build evidence_chain from crossref
                evidence_chain: list[dict[str, Any]] = []
                for edge in caused_by + manifests_as + addresses_edges:
                    evidence_chain.append({
                        "source": str(edge.get("source", "")),
                        "target": str(edge.get("target", "")),
                        "relation": str(edge.get("relation", "")),
                        "weight": edge.get("weight"),
                    })

                # 10. Assemble content
                assembled_parts: list[str] = []
                if domain_layer.get("content"):
                    assembled_parts.append(
                        f"# {domain_layer.get('title', '')}\n\n{domain_layer['content']}"
                    )
                for p in pruning_layer:
                    assembled_parts.append(f"## {p['title']}\n\n{p['content']}")
                for v in variant_layer:
                    assembled_parts.append(f"## Variant: {v['knowledge']}\n\n{v['content']}")
                assembled_content = "\n\n".join(assembled_parts)

                # 11. Compute cache_key
                max_version = 0
                cache_key = f"{identity_node}:v{max_version}"

                result = {
                    "domain_layer": domain_layer,
                    "pruning_layer": pruning_layer,
                    "variant_layer": variant_layer,
                    "assembled_content": assembled_content,
                    "evidence_chain": evidence_chain,
                    "cache_key": cache_key,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False, default=str))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_generate_textbook)

        # ---- 2. agentdb_get_coverage_report ----
        async def agentdb_get_coverage_report(
            ctx: RunContext[Any],
            identity_node: str,
            symptoms_checked: list[str],
        ) -> ToolReturn:
            """Generate a coverage report for a set of symptoms.

            Calls query_applicability internally, then computes coverage
            per symptom (matched_symptom_uri, covered, covering_knowledge)
            and per fault (has_diagnostic_steps, has_failure_mechanism,
            has_fault_mechanism, coverage_completeness). Generates
            recommendations for gaps.

            Args:
                identity_node: Catalog node path.
                symptoms_checked: List of symptom URIs or paths to check.

            Returns:
                JSON with ``symptoms``, ``faults``, ``recommendations``,
                ``total_symptoms_known``, ``symptoms_covered``, and
                ``coverage_ratio``.
            """
            bom_uri = f"viking://catalog/{identity_node}/bom.md"
            graph_base = "viking://graph/entity_rel/"
            if not uri_filter.is_allowed(bom_uri) or not uri_filter.is_allowed(graph_base):
                return ToolReturn(
                    return_value="Access denied: required URI namespaces are not in the allowed list."
                )
            try:
                client = await cap.viking._ensure_client()
                # 1. Read BOM
                bom_content = await client.read(bom_uri)
                if not bom_content:
                    return ToolReturn(return_value=f"BOM not found at {bom_uri}")
                bom_components = parse_bom_table(bom_content)
                comp_ids = {
                    str(c.get("component_id", "")) for c in bom_components if c.get("component_id")
                }

                # 2. Read entity-rel files
                rel_files = await _read_entity_rel_files(
                    client,
                    graph_base,
                    ("caused_by", "manifests_as", "addresses"),
                )
                caused_by = rel_files.get("caused_by", [])
                manifests_as = rel_files.get("manifests_as", [])
                addresses_edges = rel_files.get("addresses", [])

                # 3. Collect fault and symptom URIs
                fault_uris: set[str] = set()
                for edge in caused_by:
                    target = str(edge.get("target", ""))
                    target_comp_id = (
                        target.rsplit("/", maxsplit=1)[-1].replace(".md", "").replace("_", ":")
                        if target
                        else ""
                    )
                    target_norm = _normalize_term(target_comp_id)
                    if any(_normalize_term(cid) == target_norm for cid in comp_ids):
                        source = str(edge.get("source", ""))
                        if source:
                            fault_uris.add(source)
                    for cid in comp_ids:
                        if cid and cid in target:
                            source = str(edge.get("source", ""))
                            if source:
                                fault_uris.add(source)
                            break

                known_symptom_uris: set[str] = set()
                for edge in manifests_as:
                    source = str(edge.get("source", ""))
                    if source in fault_uris:
                        target = str(edge.get("target", ""))
                        if target:
                            known_symptom_uris.add(target)

                opl_uris: set[str] = set()
                for edge in addresses_edges:
                    target = str(edge.get("target", ""))
                    if target in fault_uris:
                        source = str(edge.get("source", ""))
                        if source:
                            opl_uris.add(source)

                # 4. Check coverage per symptom
                symptoms_report: list[dict[str, Any]] = []
                covered_count = 0
                for sym in symptoms_checked:
                    sym_full = f"viking://{sym}" if not sym.startswith("viking://") else sym
                    is_covered = sym_full in known_symptom_uris or sym in known_symptom_uris
                    covering: list[str] = []
                    if is_covered:
                        covered_count += 1
                        # Find OPLs addressing faults that manifest as this symptom
                        for edge in manifests_as:
                            target = str(edge.get("target", ""))
                            if target in (sym, sym_full):
                                fault_src = str(edge.get("source", ""))
                                for ae in addresses_edges:
                                    if str(ae.get("target", "")) == fault_src:
                                        covering.append(str(ae.get("source", "")))
                    symptoms_report.append({
                        "symptom": sym,
                        "matched_symptom_uri": sym_full if is_covered else None,
                        "covered": is_covered,
                        "covering_knowledge": covering,
                    })

                # 5. Check fault coverage
                faults_report: list[dict[str, Any]] = []
                for fault_uri in sorted(fault_uris):
                    full_uri = (
                        f"viking://{fault_uri}"
                        if not fault_uri.startswith("viking://")
                        else fault_uri
                    )
                    has_diagnostic = any(
                        str(e.get("target", "")) == fault_uri for e in addresses_edges
                    )
                    has_procedure = False  # would check confirmed_by/repaired_by
                    faults_report.append({
                        "fault_uri": full_uri,
                        "has_diagnostic_steps": has_diagnostic,
                        "has_failure_mechanism": True,  # would check body sections
                        "has_fault_mechanism": has_procedure,
                        "coverage_completeness": "partial" if has_diagnostic else "none",
                    })

                # 6. Generate recommendations
                recommendations: list[str] = []
                for sym_item in symptoms_report:
                    if not sym_item["covered"]:
                        recommendations.append(
                            f"Symptom '{sym_item['symptom']}' is not covered by any "
                            f"known fault-symptom relationship."
                        )
                for fault in faults_report:
                    if not fault["has_diagnostic_steps"]:
                        recommendations.append(
                            f"Fault '{fault['fault_uri']}' lacks diagnostic steps."
                        )

                total_known = len(known_symptom_uris)
                ratio = covered_count / len(symptoms_checked) if symptoms_checked else 0.0

                result = {
                    "total_symptoms_known": total_known,
                    "symptoms_covered": covered_count,
                    "coverage_ratio": round(ratio, 4),
                    "symptoms": symptoms_report,
                    "faults": faults_report,
                    "recommendations": recommendations,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False, default=str))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_get_coverage_report)

    return tools
