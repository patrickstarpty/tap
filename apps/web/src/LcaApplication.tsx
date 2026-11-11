import { useEffect, useState } from "react";
import { LcaWorkspace } from "./features/lca/LcaWorkspace";

/** The project comes from the authorized gateway, never a demo constant or URL. */
export function LcaApplication() {
  const [project, setProject] = useState<string>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch("/api/lca/context", {
          credentials: "same-origin",
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok)
          throw new Error("Authorized LCA project context is unavailable.");
        const context: unknown = await response.json();
        if (
          !context ||
          typeof context !== "object" ||
          !("project_id" in context) ||
          typeof context.project_id !== "string" ||
          !context.project_id ||
          !("available" in context) ||
          context.available !== true
        ) {
          throw new Error(
            "LCA gateway is unavailable. Configure the authorized backend connection before using real debug flows.",
          );
        }
        if (!controller.signal.aborted) setProject(context.project_id);
      } catch (failure) {
        if (!controller.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : "LCA project context is unavailable.",
          );
      }
    }
    void load();
    return () => controller.abort();
  }, []);
  return (
    <section
      className="tap-lca-application"
      aria-label="Low Code Automation workspace"
    >
      {error ? (
        <p role="alert">{error}</p>
      ) : project ? (
        <LcaWorkspace projectId={project} />
      ) : (
        <p role="status">Loading authorized project…</p>
      )}
    </section>
  );
}
