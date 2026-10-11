from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .models import CanonicalBranch, CanonicalMessageNode, CanonicalRole


def as_mapping(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def as_list(value: object) -> list[Any]:
    if isinstance(value, list):
        # Parsers may append synthesized blocks (for example attachments).  A
        # shallow copy keeps caller-owned decoded JSON immutable and parsing
        # deterministic across retries.
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return []


def source_id(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    mapping = as_mapping(value)
    if mapping:
        for key in ("id", "uuid", "message_id", "message_uuid"):
            identifier = source_id(mapping.get(key))
            if identifier:
                return identifier
    return None


def unique_id(candidate: str, used: set[str]) -> str:
    if candidate not in used:
        used.add(candidate)
        return candidate
    suffix = 2
    while f"{candidate}#{suffix}" in used:
        suffix += 1
    result = f"{candidate}#{suffix}"
    used.add(result)
    return result


def normalize_timestamp(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    parsed: datetime | None = None
    if isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
    if parsed is None:
        return None
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_role(value: object, aliases: Mapping[str, CanonicalRole]) -> CanonicalRole:
    if not isinstance(value, str):
        return "unknown"
    return aliases.get(value.strip().lower(), "unknown")


def unknown_fields(value: Mapping[str, Any], known: Iterable[str]) -> dict[str, Any]:
    known_set = set(known)
    return {key: deepcopy(item) for key, item in value.items() if key not in known_set}


def first_present(value: Mapping[str, Any], keys: Iterable[str]) -> tuple[bool, Any]:
    for key in keys:
        if key in value:
            return True, value[key]
    return False, None


def finish_graph(
    nodes: list[CanonicalMessageNode],
    current_node_id: str | None,
) -> tuple[list[str], list[CanonicalBranch]]:
    """Complete parent-derived child links and enumerate every root-to-leaf path."""

    if not nodes:
        return [], []
    by_id = {node.id: node for node in nodes}
    for node in nodes:
        if node.parent_id in by_id:
            parent = by_id[node.parent_id]
            if node.id not in parent.children_ids:
                parent.children_ids.append(node.id)

    roots = [node.id for node in nodes if not node.parent_id or node.parent_id not in by_id]
    if not roots:
        roots = [nodes[0].id]

    paths: list[list[str]] = []
    covered: set[str] = set()

    def visit(root_id: str) -> None:
        """Enumerate root-to-leaf paths without Python recursion.

        Real exports can contain conversations with thousands of messages.
        Keeping traversal iterative prevents a valid long conversation from
        failing at Python's recursion limit while retaining every branch.
        """

        path: list[str] = []
        path_ids: set[str] = set()
        # Each frame is ``(node id, next child index, eligible children)``.
        # ``children is None`` marks a node that has not been entered yet.
        stack: list[tuple[str, int, list[str] | None]] = [(root_id, 0, None)]
        while stack:
            node_id, child_index, children = stack[-1]
            if children is None:
                path.append(node_id)
                path_ids.add(node_id)
                covered.add(node_id)
                children = list(dict.fromkeys(
                    child
                    for child in by_id[node_id].children_ids
                    if child in by_id and child not in path_ids
                ))
                stack[-1] = (node_id, 0, children)
                if not children:
                    paths.append(list(path))
                continue

            if child_index < len(children):
                child_id = children[child_index]
                stack[-1] = (node_id, child_index + 1, children)
                if child_id not in path_ids:
                    stack.append((child_id, 0, None))
                continue

            stack.pop()
            path.pop()
            path_ids.remove(node_id)

    for root_id in roots:
        visit(root_id)
    for node in nodes:
        if node.id not in covered:
            roots.append(node.id)
            visit(node.id)

    branches: list[CanonicalBranch] = []
    seen_paths: set[tuple[str, ...]] = set()
    for path in paths:
        key = tuple(path)
        if not path or key in seen_paths:
            continue
        seen_paths.add(key)
        leaf_id = path[-1]
        source_branch_id = next(
            (by_id[node_id].provenance.branch_id for node_id in reversed(path) if by_id[node_id].provenance.branch_id),
            None,
        )
        branches.append(
            CanonicalBranch(
                id=f"branch:{leaf_id}",
                node_ids=path,
                leaf_node_id=leaf_id,
                is_current=leaf_id == current_node_id,
                source_branch_id=source_branch_id,
            )
        )
    return list(dict.fromkeys(roots)), branches
