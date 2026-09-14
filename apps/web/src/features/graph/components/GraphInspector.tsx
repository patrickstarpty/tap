import type { GraphEdge, GraphEvidenceLink, GraphNode } from "../model/graph";
import "./graph.css";

export function GraphInspector({
  node,
  relations,
  evidence,
}: {
  node: GraphNode;
  relations: GraphEdge[];
  evidence: GraphEvidenceLink[];
}) {
  return (
    <aside className="tap-graph-inspector" aria-label={`${node.label} details`}>
      <header>
        <h2>{node.label}</h2>
        <p>{node.nodeType}</p>
      </header>
      <dl>
        <div>
          <dt>Canonical key</dt>
          <dd>{node.canonicalKey}</dd>
        </div>
        <div>
          <dt>Community</dt>
          <dd>{node.community ?? "Unassigned"}</dd>
        </div>
      </dl>
      <h3>Relations</h3>
      <ul>
        {relations.map((relation) => (
          <li key={relation.edgeId}>
            <strong>{relation.relationType}</strong>
            <span>{relation.origin}</span>
            <small>{Math.round(relation.confidence * 100)}% confidence</small>
          </li>
        ))}
      </ul>
      <h3>Evidence</h3>
      {evidence.length ? (
        <ul>
          {evidence.map((item) => (
            <li key={item.evidenceId}>
              <a href={item.href}>{item.label}</a>
            </li>
          ))}
        </ul>
      ) : (
        <p>
          No direct evidence. Inspect inference provenance before using this
          relation.
        </p>
      )}
    </aside>
  );
}
