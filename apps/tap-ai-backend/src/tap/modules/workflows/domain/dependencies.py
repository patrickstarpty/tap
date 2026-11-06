"""DAG guard used before persisting a workflow dependency edge."""

from collections.abc import Iterable


def validate_dependency_dag(
    edges: Iterable[tuple[str, str]], from_node_id: str, to_node_id: str
) -> None:
    """Reject an edge if its destination already reaches its source."""
    if from_node_id == to_node_id:
        raise ValueError("workflow dependency cannot refer to itself")
    outgoing: dict[str, set[str]] = {}
    for source, target in edges:
        outgoing.setdefault(source, set()).add(target)
    pending = [to_node_id]
    visited: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id == from_node_id:
            raise ValueError("workflow dependency would create a cycle")
        if node_id not in visited:
            visited.add(node_id)
            pending.extend(outgoing.get(node_id, ()))
