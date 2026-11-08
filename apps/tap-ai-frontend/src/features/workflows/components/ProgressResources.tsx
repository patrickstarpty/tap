import type { WorkflowTaskSnapshot } from "../api/client";

const terminal = ["succeeded", "failed", "canceled", "skipped", "unknown"];
function relativeTime(value: string) {
  const minutes = Math.max(
    0,
    Math.floor((Date.now() - Date.parse(value)) / 60000),
  );
  return minutes < 1
    ? "Just now"
    : minutes < 60
      ? `${minutes}m ago`
      : minutes < 1440
        ? `${Math.floor(minutes / 60)}h ago`
        : `${Math.floor(minutes / 1440)}d ago`;
}
export function ProgressResources({
  snapshot,
  onSelectNode,
}: {
  snapshot: WorkflowTaskSnapshot;
  onSelectNode: (id: string) => void;
}) {
  const nodes = snapshot.nodes.filter(
    (node) => node.kind === "agent" && node.parentNodeId !== null,
  );
  const active = nodes.filter((node) => !terminal.includes(node.status));
  const done = nodes.filter((node) => terminal.includes(node.status));
  const sources = [
    ...new Set(
      snapshot.events
        .filter((event) => event.kind === "browser.action_finished")
        .flatMap((event) => {
          const value = event.payload.url;
          if (typeof value !== "string") return [];
          try {
            const url = new URL(value);
            return ["http:", "https:"].includes(url.protocol)
              ? [`${url.origin}${url.pathname}`]
              : [];
          } catch {
            return [];
          }
        }),
    ),
  ];
  return (
    <>
      <section aria-label="Subagents" className="tap-progress-resource">
        <details>
          <summary>
            <span>Subagents</span>
            <span>
              {active.length ? `${active.length} active · ` : ""}
              {done.length} done
            </span>
          </summary>
          {[
            { name: "Active", items: active },
            { name: "Done", items: done },
          ].map((group) => (
            <div key={group.name} className="tap-agent-group">
              <h3>
                {group.name} · {group.items.length}
              </h3>
              {!group.items.length ? (
                <p>No {group.name.toLowerCase()} subagents</p>
              ) : (
                <ul>
                  {group.items.map((node) => {
                    const last = [...snapshot.events]
                      .reverse()
                      .find(
                        (event) =>
                          event.nodeId === node.nodeId ||
                          event.payload.node_id === node.nodeId,
                      );
                    return (
                      <li key={node.nodeId}>
                        <button
                          type="button"
                          onClick={() => onSelectNode(node.nodeId)}
                        >
                          <span className="tap-agent-symbol" aria-hidden="true">
                            ✳
                          </span>
                          <span>
                            {node.displayName}
                            <small data-status={node.status}>
                              {node.status === "succeeded"
                                ? "Completed"
                                : node.status === "unknown"
                                  ? "Outcome unknown"
                                  : node.status.replaceAll("_", " ")}
                            </small>
                          </span>
                          {last ? (
                            <time dateTime={last.occurredAt}>
                              {relativeTime(last.occurredAt)}
                            </time>
                          ) : null}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          ))}
        </details>
      </section>
      <section aria-label="Sources" className="tap-progress-resource">
        <details>
          <summary>
            <span>Sources</span>
            <span>
              {sources.length ? `${sources.length} visited` : "No sources yet"}
            </span>
          </summary>
          {sources.length ? (
            <ul className="tap-progress-sources">
              {sources.map((url) => (
                <li key={url}>
                  <a href={url} target="_blank" rel="noopener noreferrer">
                    {new URL(url).hostname}
                    <small>{new URL(url).pathname}</small>
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p>Visited pages will appear here.</p>
          )}
        </details>
      </section>
    </>
  );
}
