"""Schema-aware read tools for AgentDBCapability.

Phase 2: Knowledge-schema-aware tools that understand the catalog, BOM,
fault-symptom graph, variant merging, and knowledge applicability structures.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import RunContext  # noqa: TC002 - needed for get_type_hints()
import yaml

from agentpool.capabilities.agent_db.helpers import (
    merge_variant_sections,
    parse_bom_table,
    parse_frontmatter,
)
from agentpool.capabilities.agent_db.visibility import URIPrefixFilter


if TYPE_CHECKING:
    from collections.abc import Callable

    from agentpool.capabilities.agent_db import AgentDBCapability


# Entity-rel YAML files under viking://graph/entity_rel/
_ENTITY_REL_FILES = (
    "manifests_as",
    "caused_by",
    "leads_to",
    "co_occurs_with",
    "addresses",
    "confirmed_by",
    "repaired_by",
)

# Map directory-type segments in entity paths to knowledge-type labels
_PATH_TYPE_MAP = {
    "fault": "fault",
    "symptom": "symptom",
    "opl": "opl",
    "procedure": "procedure",
    "component": "component",
}


def _normalize_term(term: str) -> str:
    """Normalize a search term for comparison.

    Lowercases, strips dashes and spaces so that
    ``"SY75C"``, ``"SY-75C"``, ``"sy 75c"`` all match ``"sy75c"``.

    Args:
        term: The raw search term.

    Returns:
        The normalized term.
    """
    return term.lower().replace("-", "").replace(" ", "")


def _entity_type_from_path(path: str) -> str | None:
    """Extract the knowledge type from an entity path segment.

    Looks for one of ``fault/``, ``symptom/``, ``opl/``, ``procedure/``,
    ``component/`` in the path and returns the corresponding label.

    Args:
        path: An entity path (e.g. ``wiki/fault/pump_failure``).

    Returns:
        The type label or ``None`` if no known type segment is found.
    """
    lower = path.lower()
    for seg, label in _PATH_TYPE_MAP.items():
        if f"/{seg}/" in lower or lower.startswith(f"{seg}/"):
            return label
    return None


def _entity_uri_from_path(path: str) -> str:
    """Build a viking:// URI from an entity path.

    If the path already starts with ``viking://`` it is returned as-is.
    Otherwise ``viking://`` is prepended.

    Args:
        path: The entity path.

    Returns:
        A full viking:// URI.
    """
    if path.startswith("viking://"):
        return path
    return f"viking://{path}"


async def _ls_recursive(
    client: Any,
    uri: str,
    depth: int = 0,
    max_depth: int = 5,
) -> list[str]:
    """Recursively walk a directory tree via ``client.ls()``.

    Returns a flat list of all entry names (both files and directories)
    encountered.  Each entry is the last path component as returned by
    the SDK.

    Args:
        client: The Viking SDK client.
        uri: The directory URI to list.
        depth: Current recursion depth (internal).
        max_depth: Maximum recursion depth.

    Returns:
        A list of entry name strings.
    """
    if depth > max_depth:
        return []
    results: list[str] = []
    try:
        entries = await client.ls(uri)
    except Exception:
        return results
    if not entries:
        return results
    for entry in entries:
        if isinstance(entry, str):
            name = entry
            is_dir = entry.endswith("/")
        elif isinstance(entry, dict):
            name = entry.get("name", "")
            is_dir = entry.get("is_dir", False)
        else:
            continue
        results.append(name)
        if is_dir and name:
            sub_uri = uri.rstrip("/") + "/" + name
            results.extend(await _ls_recursive(client, sub_uri, depth + 1, max_depth))
    return results


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
        Dict mapping file name → list of edge dicts (empty list on failure).
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


def build_schema_read_tools(
    cap: AgentDBCapability,
) -> list[Callable[..., Any]]:
    """Build schema-aware read tool functions for AgentDBCapability.

    Returns 5 async tool closures:
    - ``agentdb_resolve_identity`` — resolve serial/model to catalog node
    - ``agentdb_traverse_bom`` — read and parse a device BOM
    - ``agentdb_get_fault_symptom_graph`` — build fault/symptom relation graph
    - ``agentdb_get_effective_knowledge`` — merge wiki entity with variant
    - ``agentdb_query_applicability`` — query applicable knowledge for a device

    Args:
        cap: The AgentDBCapability instance that owns these tools.

    Returns:
        A list of 5 async tool functions.
    """
    tools: list[Callable[..., Any]] = []
    uri_filter = URIPrefixFilter(allowed_prefixes=cap.allowed_prefixes)

    if cap.mode in ("read", "write", "all"):
        # ---- 1. agentdb_resolve_identity ----
        async def agentdb_resolve_identity(
            ctx: RunContext[Any],
            serial_number: str = "",
            marketing_model: str = "",
            rd_model: str = "",
        ) -> ToolReturn:
            """Resolve a serial number or model name to a catalog identity node.

            Walks the ``viking://catalog/`` directory tree recursively and
            matches directory names against the provided search term
            (normalized: lowercase, dashes and spaces stripped).

            Args:
                serial_number: Serial number (e.g. ``"SY75C-12345"``).
                marketing_model: Marketing model name (e.g. ``"SY75C"``).
                rd_model: R&D model code (e.g. ``"SY75C"``).

            Returns:
                JSON with identity node path, catalog URI, BOM URI, and
                domain URI; or an error message string if not found.
            """
            search_term = serial_number or marketing_model or rd_model
            if not search_term:
                return ToolReturn(
                    return_value="Error: at least one of serial_number, marketing_model, or rd_model must be provided."
                )
            catalog_uri = "viking://catalog/"
            if not uri_filter.is_allowed(catalog_uri):
                return ToolReturn(
                    return_value=f"Access denied: URI '{catalog_uri}' is not in the allowed namespaces for this agent."
                )
            try:
                client = await cap.viking._ensure_client()
                # Search the catalog tree for a directory matching the normalized term
                matched_path = await _find_catalog_path(client, catalog_uri, search_term)
                if matched_path is None:
                    return ToolReturn(
                        return_value=f"Identity not found for '{search_term}' in catalog."
                    )
                node_path = matched_path
                catalog_node_uri = f"viking://catalog/{node_path}/"
                bom_uri = f"viking://catalog/{node_path}/bom.md"
                # Determine domain from path segments
                domain = "excavator"  # default
                parts = node_path.split("/")
                _min_domain_parts = 2
                if len(parts) >= _min_domain_parts:
                    domain = parts[1] if parts[0] in ("sany", "doosan", "komatsu") else parts[0]
                domain_uri = f"viking://wiki/domain/{domain}"
                # Level: "model" if leaf, "series" if intermediate
                _min_model_parts = 3
                level = "model" if len(parts) >= _min_model_parts else "series"
                result = {
                    "node_path": node_path,
                    "level": level,
                    "catalog_uri": catalog_node_uri,
                    "bom_uri": bom_uri,
                    "variant_bom_uri": None,
                    "domain_uri": domain_uri,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_resolve_identity)

        # ---- 2. agentdb_traverse_bom ----
        async def agentdb_traverse_bom(
            ctx: RunContext[Any],
            identity_node: str,
            system: str = "",
            depth: int = 1,
        ) -> ToolReturn:
            """Traverse the BOM (Bill of Materials) for a specific device.

            Reads ``viking://catalog/{identity_node}/bom.md`` and parses
            the BOM table. Optionally filters by system name.

            Args:
                identity_node: Catalog node path (e.g. ``"sany/excavator/sy75c"``).
                system: Optional system filter (e.g. ``"液压系统"``).
                depth: Reserved for future hierarchical traversal.

            Returns:
                JSON with device node and list of component dicts.
            """
            bom_uri = f"viking://catalog/{identity_node}/bom.md"
            if not uri_filter.is_allowed(bom_uri):
                return ToolReturn(
                    return_value=f"Access denied: URI '{bom_uri}' is not in the allowed namespaces for this agent."
                )
            try:
                client = await cap.viking._ensure_client()
                content = await client.read(bom_uri)
                if not content:
                    return ToolReturn(return_value=f"BOM not found at {bom_uri}")
                components = parse_bom_table(content)
                if system:
                    components = [c for c in components if c.get("system", "") == system]
                # Enrich each component with component_uri
                enriched: list[dict[str, Any]] = []
                for comp in components:
                    comp_id = str(comp.get("component_id", ""))
                    # Build wiki component URI: replace non-alnum with _
                    safe_id = comp_id.replace("/", "_").replace(":", "_")
                    comp_uri = f"viking://wiki/component/{safe_id}.md"
                    enriched.append({
                        "component_id": comp_id,
                        "component_uri": comp_uri,
                        "component_name": comp.get("component_name", ""),
                        "material_no": comp.get("material_no", ""),
                        "quantity": comp.get("quantity", "1"),
                        "system": comp.get("system", ""),
                        "class_ref": comp.get("class_ref", ""),
                        "ecu_family": comp.get("ecu_family"),
                        "children": [],
                    })
                result = {
                    "device": identity_node,
                    "components": enriched,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_traverse_bom)

        # ---- 3. agentdb_get_fault_symptom_graph ----
        async def agentdb_get_fault_symptom_graph(
            ctx: RunContext[Any],
            fault_uri: str = "",
            symptom_uri: str = "",
            identity_node: str = "",
        ) -> ToolReturn:
            """Build a fault-symptom-component relationship graph.

            Reads 4 entity-rel YAML files from ``viking://graph/entity_rel/``
            (manifests_as, caused_by, leads_to, co_occurs_with) and builds
            a graph of faults, symptoms, and components.

            Args:
                fault_uri: Optional fault entity URI to filter by.
                symptom_uri: Optional symptom entity URI to filter by.
                identity_node: Optional device node (reserved for future).

            Returns:
                JSON with faults, symptoms, components, edges, and
                co-occurring faults.
            """
            graph_base = "viking://graph/entity_rel/"
            if not uri_filter.is_allowed(graph_base):
                return ToolReturn(
                    return_value=f"Access denied: URI '{graph_base}' is not in the allowed namespaces for this agent."
                )
            try:
                client = await cap.viking._ensure_client()
                rel_files = await _read_entity_rel_files(
                    client,
                    graph_base,
                    ("manifests_as", "caused_by", "leads_to", "co_occurs_with"),
                )
                manifests_as = rel_files.get("manifests_as", [])
                caused_by = rel_files.get("caused_by", [])
                leads_to = rel_files.get("leads_to", [])
                co_occurs_with = rel_files.get("co_occurs_with", [])

                # Collect all edges
                all_edges: list[dict[str, Any]] = []
                for edges in (manifests_as, caused_by, leads_to, co_occurs_with):
                    all_edges.extend(edges)

                # Collect entity URIs by type
                faults: set[str] = set()
                symptoms: set[str] = set()
                components: set[str] = set()

                for edge in all_edges:
                    for key in ("source", "target"):
                        val = str(edge.get(key, ""))
                        if not val:
                            continue
                        etype = _entity_type_from_path(val)
                        if etype == "fault":
                            faults.add(val)
                        elif etype == "symptom":
                            symptoms.add(val)
                        elif etype == "component":
                            components.add(val)

                # Filter by fault_uri or symptom_uri if provided
                if fault_uri:
                    fault_uri_norm = fault_uri
                    # Keep only faults matching and edges involving it
                    faults = {
                        f
                        for f in faults
                        if f == fault_uri_norm
                        or _normalize_term(f) == _normalize_term(fault_uri_norm)
                    }
                    all_edges = [
                        e
                        for e in all_edges
                        if _normalize_term(str(e.get("source", "")))
                        == _normalize_term(fault_uri_norm)
                        or _normalize_term(str(e.get("target", "")))
                        == _normalize_term(fault_uri_norm)
                    ]
                    # Rebuild entity sets from filtered edges
                    symptoms = set()
                    components = set()
                    for edge in all_edges:
                        for key in ("source", "target"):
                            val = str(edge.get(key, ""))
                            etype = _entity_type_from_path(val)
                            if etype == "symptom":
                                symptoms.add(val)
                            elif etype == "component":
                                components.add(val)

                if symptom_uri:
                    symptom_uri_norm = symptom_uri
                    symptoms = {
                        s
                        for s in symptoms
                        if s == symptom_uri_norm
                        or _normalize_term(s) == _normalize_term(symptom_uri_norm)
                    }
                    all_edges = [
                        e
                        for e in all_edges
                        if _normalize_term(str(e.get("source", "")))
                        == _normalize_term(symptom_uri_norm)
                        or _normalize_term(str(e.get("target", "")))
                        == _normalize_term(symptom_uri_norm)
                    ]
                    faults = set()
                    components = set()
                    for edge in all_edges:
                        for key in ("source", "target"):
                            val = str(edge.get(key, ""))
                            etype = _entity_type_from_path(val)
                            if etype == "fault":
                                faults.add(val)
                            elif etype == "component":
                                components.add(val)

                # Co-occurring faults
                co_occurring: list[str] = []
                for edge in co_occurs_with:
                    src = str(edge.get("source", ""))
                    tgt = str(edge.get("target", ""))
                    if faults and (src in faults or tgt in faults):
                        co_occurring.extend(
                            val
                            for val in (src, tgt)
                            if _entity_type_from_path(val) == "fault" and val not in faults
                        )

                result = {
                    "faults": sorted(faults),
                    "symptoms": sorted(symptoms),
                    "components": sorted(components),
                    "edges": all_edges,
                    "co_occurring_faults": sorted(set(co_occurring)),
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_get_fault_symptom_graph)

        # ---- 4. agentdb_get_effective_knowledge ----
        async def agentdb_get_effective_knowledge(
            ctx: RunContext[Any],
            identity: str,
            uri: str,
        ) -> ToolReturn:
            """Get the effective knowledge for a wiki entity, applying variant overrides.

            Reads the wiki entity at ``uri``, then looks for a variant file
            in ``viking://catalog/{identity}/variant/`` whose ``knowledge``
            frontmatter field matches the entity path extracted from ``uri``.
            If found, merges the variant sections into the wiki body.

            Args:
                identity: Device identity node (e.g. ``"sany/excavator/sy75c"``).
                uri: Wiki entity URI (e.g. ``viking://wiki/fault/pump_failure.md``).

            Returns:
                JSON with merged content, variant info, and merge operations.
            """
            if not uri_filter.is_allowed(uri):
                return ToolReturn(
                    return_value=f"Access denied: URI '{uri}' is not in the allowed namespaces for this agent."
                )
            try:
                client = await cap.viking._ensure_client()
                # Read the base wiki entity
                wiki_content = await client.read(uri)
                if not wiki_content:
                    return ToolReturn(return_value=f"Entity not found at {uri}")
                frontmatter, wiki_body = parse_frontmatter(wiki_content)
                # Extract entity path from uri for matching
                # e.g. viking://wiki/fault/pump_failure.md → wiki/fault/pump_failure.md
                entity_path = uri.replace("viking://", "")

                # List variant directory
                variant_dir = f"viking://catalog/{identity}/variant/"
                variant_uri: str | None = None
                merged_content = wiki_body
                merge_ops: list[str] = []
                has_variant = False

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
                            if not v_content:
                                continue
                            v_fm, v_body = parse_frontmatter(v_content)
                            v_knowledge = str(v_fm.get("knowledge", ""))
                            if v_knowledge == entity_path:
                                merged_content, merge_ops = merge_variant_sections(
                                    wiki_body, v_body
                                )
                                variant_uri = v_file_uri
                                has_variant = True
                                break

                result = {
                    "uri": uri,
                    "identity": identity,
                    "content": merged_content,
                    "has_variant": has_variant,
                    "variant_uri": variant_uri,
                    "merge_operations": merge_ops,
                    "base_version": frontmatter.get("version", 1),
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_get_effective_knowledge)

        # ---- 5. agentdb_query_applicability ----
        async def agentdb_query_applicability(
            ctx: RunContext[Any],
            identity_node: str,
            knowledge_types: list[str] | None = None,
            exclude_disputed: bool = True,
        ) -> ToolReturn:
            """Query applicable knowledge entities for a specific device.

            Reads the device BOM and entity-rel files, then traces the
            knowledge graph to find faults, symptoms, OPLs, and procedures
            applicable to the device's components.

            Args:
                identity_node: Catalog node path (e.g. ``"sany/excavator/sy75c"``).
                knowledge_types: Optional list of types to include
                    (e.g. ``["fault", "symptom"]``).
                exclude_disputed: Skip entities with low credibility or
                    disputed status.

            Returns:
                JSON with identity node, items list, and coverage counts.
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
                    ("caused_by", "manifests_as", "addresses", "confirmed_by", "repaired_by"),
                )
                caused_by = rel_files.get("caused_by", [])
                manifests_as = rel_files.get("manifests_as", [])
                addresses_edges = rel_files.get("addresses", [])
                confirmed_by = rel_files.get("confirmed_by", [])
                repaired_by = rel_files.get("repaired_by", [])

                # 3. Match caused_by targets to BOM component_ids → collect fault URIs
                fault_uris: set[str] = set()
                for edge in caused_by:
                    target = str(edge.get("target", ""))
                    target_comp_id = (
                        target.rsplit("/", maxsplit=1)[-1].replace(".md", "").replace("_", ":")
                        if target
                        else ""
                    )
                    # Also try direct match and normalized match
                    target_norm = _normalize_term(target_comp_id)
                    if any(_normalize_term(cid) == target_norm for cid in comp_ids):
                        source = str(edge.get("source", ""))
                        if source:
                            fault_uris.add(source)
                    # Also match if target path contains a component_id
                    for cid in comp_ids:
                        if cid and cid in target:
                            source = str(edge.get("source", ""))
                            if source:
                                fault_uris.add(source)
                            break

                # 4. Match manifests_as sources to collected faults → collect symptom URIs
                symptom_uris: set[str] = set()
                for edge in manifests_as:
                    source = str(edge.get("source", ""))
                    if source in fault_uris:
                        target = str(edge.get("target", ""))
                        if target:
                            symptom_uris.add(target)

                # 5. Match addresses targets to collected faults → collect OPL URIs
                opl_uris: set[str] = set()
                for edge in addresses_edges:
                    target = str(edge.get("target", ""))
                    if target in fault_uris:
                        source = str(edge.get("source", ""))
                        if source:
                            opl_uris.add(source)

                # 6. Match confirmed_by/repaired_by sources to collected faults → collect procedure URIs
                procedure_uris: set[str] = set()
                for edge in confirmed_by:
                    source = str(edge.get("source", ""))
                    if source in fault_uris:
                        target = str(edge.get("target", ""))
                        if target:
                            procedure_uris.add(target)
                for edge in repaired_by:
                    source = str(edge.get("source", ""))
                    if source in fault_uris:
                        target = str(edge.get("target", ""))
                        if target:
                            procedure_uris.add(target)

                # 7. Read each entity's L0 abstract, parse frontmatter for metadata
                all_uris = (
                    fault_uris
                    | symptom_uris
                    | opl_uris
                    | procedure_uris
                    | comp_ids_as_uris(bom_components)
                )
                items: list[dict[str, Any]] = []
                for entity_uri in sorted(all_uris):
                    full_uri = (
                        _entity_uri_from_path(entity_uri)
                        if not entity_uri.startswith("viking://")
                        else entity_uri
                    )
                    etype = _entity_type_from_path(full_uri)
                    if etype is None:
                        continue
                    if knowledge_types and etype not in knowledge_types:
                        continue
                    try:
                        abstract = await client.abstract(full_uri)
                    except Exception:
                        abstract = ""
                    if abstract:
                        fm, _ = parse_frontmatter(abstract)
                    else:
                        fm = {}
                    credibility = str(fm.get("credibility", ""))
                    status = str(fm.get("status", ""))
                    if exclude_disputed and (credibility == "low" or status == "disputed"):
                        continue
                    items.append({
                        "uri": full_uri,
                        "type": etype,
                        "title": fm.get("title", ""),
                        "credibility": credibility,
                        "version": fm.get("version", 1),
                        "status": status,
                    })

                # 8. Sort by specificity (type order: fault > symptom > opl > procedure > component)
                type_order = {"fault": 0, "symptom": 1, "opl": 2, "procedure": 3, "component": 4}
                items.sort(key=lambda x: type_order.get(x["type"], 99))

                # 9. Calculate coverage counts per type
                coverage: dict[str, int] = {
                    "fault": sum(1 for i in items if i["type"] == "fault"),
                    "symptom": sum(1 for i in items if i["type"] == "symptom"),
                    "opl": sum(1 for i in items if i["type"] == "opl"),
                    "procedure": sum(1 for i in items if i["type"] == "procedure"),
                    "component": sum(1 for i in items if i["type"] == "component"),
                }
                result = {
                    "identity_node": identity_node,
                    "items": items,
                    "coverage": coverage,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_query_applicability)

        # ---- 6. agentdb_list_pending ----
        async def agentdb_list_pending(
            ctx: RunContext[Any],
            qt_type: str = "",
            ticket_status: str = "",
            expert_owner: str = "",
            parent_qt: str = "",
            limit: int = 50,
            offset: int = 0,
        ) -> ToolReturn:
            """List pending quality tickets (QTs) across all ticket namespaces.

            Scans ``viking://tickets/opa/``, ``viking://tickets/ops/``, and
            ``viking://tickets/opl_proposal/`` for .md files, reads each
            file's frontmatter, and returns a filtered list of QTSummary
            objects.

            Args:
                qt_type: Filter by QT type (``"opa"``, ``"ops"``, ``"opl_proposal"``).
                    Empty string returns all types.
                ticket_status: Filter by ticket status (e.g. ``"open"``,
                    ``"reviewing"``, ``"approved"``).
                expert_owner: Filter by expert owner name.
                parent_qt: Filter by parent QT URI.
                limit: Maximum number of results to return.
                offset: Number of results to skip (for pagination).

            Returns:
                JSON array of QTSummary objects.
            """
            tickets_base = "viking://tickets/"
            if not uri_filter.is_allowed(tickets_base):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{tickets_base}' is not in the allowed "
                        f"namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                all_qt_dirs: tuple[str, ...] = ("opa", "ops", "opl_proposal")
                qt_dirs: tuple[str, ...] = all_qt_dirs
                if qt_type:
                    qt_dirs = (qt_type,) if qt_type in all_qt_dirs else ()
                summaries: list[dict[str, Any]] = []
                for qd in qt_dirs:
                    dir_uri = f"{tickets_base}{qd}/"
                    try:
                        entries = await client.ls(dir_uri)
                    except Exception:
                        entries = []
                    if not entries:
                        continue
                    for entry in entries:
                        if isinstance(entry, str):
                            fname = entry
                        elif isinstance(entry, dict):
                            fname = entry.get("name", "")
                        else:
                            continue
                        if not fname.endswith(".md"):
                            continue
                        file_uri = dir_uri + fname
                        try:
                            content = await client.read(file_uri)
                        except Exception:
                            continue
                        if not content:
                            continue
                        fm, _ = parse_frontmatter(content)
                        if ticket_status and str(fm.get("ticket_status", "")) != ticket_status:
                            continue
                        if expert_owner and str(fm.get("expert_owner", "")) != expert_owner:
                            continue
                        if parent_qt and str(fm.get("parent_qt", "")) != parent_qt:
                            continue
                        summaries.append({
                            "uri": file_uri,
                            "qt_type": str(fm.get("type", qd)),
                            "title": str(fm.get("title", "")),
                            "ticket_status": str(fm.get("ticket_status", "")),
                            "expert_owner": str(fm.get("expert_owner", "")),
                            "parent_qt": str(fm.get("parent_qt", "")),
                            "created_at": str(fm.get("created_at", "")),
                            "description": str(fm.get("description", "")),
                        })
                # Sort by created_at descending, then apply pagination
                summaries.sort(key=lambda x: x.get("created_at", ""), reverse=True)
                total = len(summaries)
                paginated = summaries[offset : offset + limit]
                result = {
                    "items": paginated,
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                }
                return ToolReturn(return_value=json.dumps(result["items"], ensure_ascii=False))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_list_pending)

        # ---- 7. agentdb_get_qt ----
        async def agentdb_get_qt(
            ctx: RunContext[Any],
            qt_uri: str,
        ) -> ToolReturn:
            """Read a quality ticket (QT) file and return its full detail.

            Parses the QT file frontmatter and body, extracts CR (change
            record) history from HTML comments in the body, and returns
            a QTDetail JSON object.

            Args:
                qt_uri: URI of the QT file (e.g. ``viking://tickets/opa/opa-001.md``).

            Returns:
                JSON with ``uri``, ``frontmatter``, ``body``, and ``cr_history``.
            """
            if not uri_filter.is_allowed(qt_uri):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{qt_uri}' is not in the allowed "
                        f"namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                content = await client.read(qt_uri)
                if not content:
                    return ToolReturn(return_value=f"QT not found at {qt_uri}")
                fm, body = parse_frontmatter(content)
                # Extract CR history from HTML comments: <!-- cr: ... -->
                cr_history: list[dict[str, str]] = []
                cr_pattern = re.compile(r"<!--\s*cr:\s*(.+?)\s*-->", re.DOTALL)
                for m in cr_pattern.finditer(body):
                    cr_text = m.group(1).strip()
                    entry: dict[str, str] = {}
                    for raw_part in cr_text.split("|"):
                        part = raw_part.strip()
                        if ":" in part:
                            key, _, val = part.partition(":")
                            entry[key.strip()] = val.strip()
                    if entry:
                        cr_history.append(entry)
                result = {
                    "uri": qt_uri,
                    "frontmatter": fm,
                    "body": body,
                    "cr_history": cr_history,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False, default=str))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_get_qt)

        # ---- 8. agentdb_get_context ----
        async def agentdb_get_context(
            ctx: RunContext[Any],
            qt_uri: str,
        ) -> ToolReturn:
            """Get the context of a QT including raw references and graph relationships.

            Reads the QT file, extracts raw references from frontmatter
            ``entity_rel`` field and crossref graph file, and returns
            a QTContext JSON object.

            Args:
                qt_uri: URI of the QT file.

            Returns:
                JSON with ``qt_uri``, ``raw_refs``, ``related_entities``, and
                ``graph_context``.
            """
            if not uri_filter.is_allowed(qt_uri):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{qt_uri}' is not in the allowed "
                        f"namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                content = await client.read(qt_uri)
                if not content:
                    return ToolReturn(return_value=f"QT not found at {qt_uri}")
                fm, _ = parse_frontmatter(content)
                # Extract raw_refs from frontmatter entity_rel
                raw_refs: list[dict[str, Any]] = []
                entity_rel = fm.get("entity_rel", [])
                if isinstance(entity_rel, list):
                    raw_refs = [e for e in entity_rel if isinstance(e, dict)]
                # Read crossref graph file for related entities
                related_entities: list[dict[str, Any]] = []
                graph_context: dict[str, Any] = {}
                crossref_uri = "viking://graph/entity_rel/crossref.yaml"
                if uri_filter.is_allowed(crossref_uri):
                    try:
                        crossref_content = await client.read(crossref_uri)
                        if crossref_content:
                            parsed = yaml.safe_load(crossref_content)
                            if isinstance(parsed, list):
                                # Filter edges related to this QT
                                qt_path = qt_uri.replace("viking://", "")
                                for edge in parsed:
                                    if isinstance(edge, dict):
                                        src = str(edge.get("source", ""))
                                        tgt = str(edge.get("target", ""))
                                        if qt_path in src or qt_path in tgt:
                                            related_entities.append(edge)
                                graph_context["crossref_edges"] = related_entities
                    except Exception:
                        pass
                result = {
                    "qt_uri": qt_uri,
                    "raw_refs": raw_refs,
                    "related_entities": related_entities,
                    "graph_context": graph_context,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_get_context)

        # ---- 9. agentdb_get_sub_qts ----
        async def agentdb_get_sub_qts(
            ctx: RunContext[Any],
            parent_uri: str,
        ) -> ToolReturn:
            """List child QTs that reference a parent QT.

            Scans all ticket subdirectories for .md files whose
            ``parent_qt`` frontmatter field matches ``parent_uri``.

            Args:
                parent_uri: URI of the parent QT.

            Returns:
                JSON array of QTSummary objects for child QTs.
            """
            tickets_base = "viking://tickets/"
            if not uri_filter.is_allowed(tickets_base):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{tickets_base}' is not in the "
                        f"allowed namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                qt_dirs: tuple[str, ...] = ("opa", "ops", "opl_proposal")
                children: list[dict[str, Any]] = []
                for qd in qt_dirs:
                    dir_uri = f"{tickets_base}{qd}/"
                    try:
                        entries = await client.ls(dir_uri)
                    except Exception:
                        entries = []
                    if not entries:
                        continue
                    for entry in entries:
                        if isinstance(entry, str):
                            fname = entry
                        elif isinstance(entry, dict):
                            fname = entry.get("name", "")
                        else:
                            continue
                        if not fname.endswith(".md"):
                            continue
                        file_uri = dir_uri + fname
                        try:
                            file_content = await client.read(file_uri)
                        except Exception:
                            continue
                        if not file_content:
                            continue
                        fm, _ = parse_frontmatter(file_content)
                        if str(fm.get("parent_qt", "")) == parent_uri:
                            children.append({
                                "uri": file_uri,
                                "qt_type": str(fm.get("type", qd)),
                                "title": str(fm.get("title", "")),
                                "ticket_status": str(fm.get("ticket_status", "")),
                                "parent_qt": str(fm.get("parent_qt", "")),
                                "created_at": str(fm.get("created_at", "")),
                            })
                return ToolReturn(return_value=json.dumps(children, ensure_ascii=False))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_get_sub_qts)

        # ---- 10. agentdb_query_signals ----
        async def agentdb_query_signals(
            ctx: RunContext[Any],
            signal_name: str = "",
            priority: str = "",
            status: str = "",
        ) -> ToolReturn:
            """Scan tickets for signal metadata and return matching signals.

            Scans all ticket subdirectories for .md files whose
            frontmatter contains a ``signal_name`` field, filters by
            the provided criteria, and returns a list of SignalInfo
            objects.

            Args:
                signal_name: Filter by signal name.
                priority: Filter by signal priority.
                status: Filter by ticket status.

            Returns:
                JSON array of SignalInfo objects.
            """
            tickets_base = "viking://tickets/"
            if not uri_filter.is_allowed(tickets_base):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{tickets_base}' is not in the "
                        f"allowed namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                qt_dirs: tuple[str, ...] = ("opa", "ops", "opl_proposal")
                signals: list[dict[str, Any]] = []
                for qd in qt_dirs:
                    dir_uri = f"{tickets_base}{qd}/"
                    try:
                        entries = await client.ls(dir_uri)
                    except Exception:
                        entries = []
                    if not entries:
                        continue
                    for entry in entries:
                        if isinstance(entry, str):
                            fname = entry
                        elif isinstance(entry, dict):
                            fname = entry.get("name", "")
                        else:
                            continue
                        if not fname.endswith(".md"):
                            continue
                        file_uri = dir_uri + fname
                        try:
                            file_content = await client.read(file_uri)
                        except Exception:
                            continue
                        if not file_content:
                            continue
                        fm, _ = parse_frontmatter(file_content)
                        if "signal_name" not in fm:
                            continue
                        if signal_name and str(fm.get("signal_name", "")) != signal_name:
                            continue
                        if priority and str(fm.get("signal_priority", "")) != priority:
                            continue
                        if status and str(fm.get("ticket_status", "")) != status:
                            continue
                        signals.append({
                            "uri": file_uri,
                            "signal_name": str(fm.get("signal_name", "")),
                            "signal_priority": str(fm.get("signal_priority", "")),
                            "ticket_status": str(fm.get("ticket_status", "")),
                            "created_at": str(fm.get("created_at", "")),
                            "qt_type": str(fm.get("type", qd)),
                        })
                return ToolReturn(return_value=json.dumps(signals, ensure_ascii=False, default=str))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_query_signals)

        # ---- 11. agentdb_query_backlog ----
        async def agentdb_query_backlog(
            ctx: RunContext[Any],
            qt_type: str = "",
            expert_owner: str = "",
        ) -> ToolReturn:
            """Aggregate pending QTs into a backlog report.

            Collects all pending QTs (reusing list_pending logic),
            computes counts by type, expert owner, and priority,
            and returns a BacklogReport JSON object.

            Args:
                qt_type: Filter by QT type.
                expert_owner: Filter by expert owner.

            Returns:
                JSON with ``total``, ``counts_by_type``, ``counts_by_expert``,
                ``items``, and ``oldest_pending_days``.
            """
            tickets_base = "viking://tickets/"
            if not uri_filter.is_allowed(tickets_base):
                return ToolReturn(
                    return_value=(
                        f"Access denied: URI '{tickets_base}' is not in the "
                        f"allowed namespaces for this agent."
                    )
                )
            try:
                client = await cap.viking._ensure_client()
                all_qt_dirs: tuple[str, ...] = ("opa", "ops", "opl_proposal")
                scan_dirs: tuple[str, ...] = all_qt_dirs
                if qt_type:
                    scan_dirs = (qt_type,) if qt_type in all_qt_dirs else ()
                items: list[dict[str, Any]] = []
                for qd in scan_dirs:
                    dir_uri = f"{tickets_base}{qd}/"
                    try:
                        entries = await client.ls(dir_uri)
                    except Exception:
                        entries = []
                    if not entries:
                        continue
                    for entry in entries:
                        if isinstance(entry, str):
                            fname = entry
                        elif isinstance(entry, dict):
                            fname = entry.get("name", "")
                        else:
                            continue
                        if not fname.endswith(".md"):
                            continue
                        file_uri = dir_uri + fname
                        try:
                            file_content = await client.read(file_uri)
                        except Exception:
                            continue
                        if not file_content:
                            continue
                        fm, _ = parse_frontmatter(file_content)
                        if expert_owner and str(fm.get("expert_owner", "")) != expert_owner:
                            continue
                        items.append({
                            "uri": file_uri,
                            "qt_type": str(fm.get("type", qd)),
                            "title": str(fm.get("title", "")),
                            "ticket_status": str(fm.get("ticket_status", "")),
                            "expert_owner": str(fm.get("expert_owner", "")),
                            "created_at": str(fm.get("created_at", "")),
                        })
                # Compute counts
                counts_by_type: dict[str, int] = {}
                counts_by_expert: dict[str, int] = {}
                for item in items:
                    qt = str(item.get("qt_type", ""))
                    counts_by_type[qt] = counts_by_type.get(qt, 0) + 1
                    exp = str(item.get("expert_owner", ""))
                    if exp:
                        counts_by_expert[exp] = counts_by_expert.get(exp, 0) + 1
                result = {
                    "total": len(items),
                    "counts_by_type": counts_by_type,
                    "counts_by_expert": counts_by_expert,
                    "items": items,
                }
                return ToolReturn(return_value=json.dumps(result, ensure_ascii=False, default=str))
            except Exception as e:
                return ToolReturn(return_value=f"Error: {e}")

        tools.append(agentdb_query_backlog)

    return tools


async def _find_catalog_path(
    client: Any,
    base_uri: str,
    target_name: str,
    current_path: str = "",
    depth: int = 0,
    max_depth: int = 6,
) -> str | None:
    """Recursively search the catalog tree for a directory matching ``target_name``.

    Args:
        client: The Viking SDK client.
        base_uri: The directory URI to search.
        target_name: The directory name to find (normalized comparison).
        current_path: Accumulated path from catalog root (internal).
        depth: Current recursion depth (internal).
        max_depth: Maximum depth.

    Returns:
        The full path from catalog root (e.g. ``"sany/excavator/sy75c"``) or None.
    """
    if depth > max_depth:
        return None
    try:
        entries = await client.ls(base_uri)
    except Exception:
        return None
    if not entries:
        return None
    target_norm = _normalize_term(target_name)
    for entry in entries:
        if isinstance(entry, str):
            name = entry
            is_dir = entry.endswith("/")
        elif isinstance(entry, dict):
            name = entry.get("name", "")
            is_dir = entry.get("is_dir", False)
        else:
            continue
        clean_name = name.rstrip("/")
        path_segment = current_path + "/" + clean_name if current_path else clean_name
        if is_dir:
            if _normalize_term(clean_name) == target_norm:
                return path_segment
            sub_uri = base_uri.rstrip("/") + "/" + name
            found = await _find_catalog_path(
                client, sub_uri, target_name, path_segment, depth + 1, max_depth
            )
            if found is not None:
                return found
    return None


def comp_ids_as_uris(components: list[dict[str, Any]]) -> set[str]:
    """Convert BOM component_ids to wiki component URIs.

    Args:
        components: List of BOM component dicts.

    Returns:
        A set of wiki component URI strings.
    """
    uris: set[str] = set()
    for comp in components:
        comp_id = str(comp.get("component_id", ""))
        if not comp_id:
            continue
        safe_id = comp_id.replace("/", "_").replace(":", "_")
        uris.add(f"viking://wiki/component/{safe_id}.md")
    return uris
