import { Button, Input } from "antd";
import { useState, type FormEvent } from "react";
import {
  CatalogError,
  catalogErrorMessage,
  managementCatalog,
  type McpServerCreateRequest,
  type McpServerView,
  type ToolView,
} from "../../../features/knowledge/api/aiAssets";

export function ToolsPanel({
  projectId,
  tools,
  servers,
  zh,
  onChanged,
}: {
  projectId: string;
  tools: ToolView[];
  servers: McpServerView[];
  zh: boolean;
  onChanged: () => Promise<void>;
}) {
  const [registering, setRegistering] = useState(false);
  const [draft, setDraft] = useState<McpServerCreateRequest>({
    slug: "",
    displayName: "",
    url: "",
    authHeaderEnv: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const run = async (action: () => Promise<unknown>) => {
    setPending(true);
    setError(null);
    try {
      await action();
      await onChanged();
      setRegistering(false);
    } catch (e) {
      setError(catalogErrorMessage(e, zh));
    } finally {
      setPending(false);
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void run(() =>
      managementCatalog.registerServer(projectId, {
        ...draft,
        authHeaderEnv: draft.authHeaderEnv || null,
      }),
    );
  };
  return (
    <section aria-label={zh ? "工具与 MCP" : "Tools and MCP"}>
      <h2>{zh ? "内置工具" : "Built-in tools"}</h2>
      <ul className="tap-catalog-list tap-tool-list">
        {tools
          .filter((t) => t.origin === "builtin")
          .map((tool) => (
            <li key={tool.toolId}>
              <article>
                <h3>
                  {tool.displayName} <small>{tool.effect}</small>
                </h3>
                <p>{tool.description}</p>
                <code>{tool.toolId}</code>
              </article>
            </li>
          ))}
      </ul>
      <div className="tap-catalog-toolbar">
        <h2>MCP servers</h2>
        <Button onClick={() => setRegistering(!registering)}>
          {zh ? "注册 MCP 服务" : "Register MCP server"}
        </Button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
      {registering ? (
        <form onSubmit={submit} className="tap-catalog-server-form">
          {(
            [
              ["slug", zh ? "标识" : "Slug"],
              ["displayName", zh ? "名称" : "Server name"],
              ["url", "URL"],
              [
                "authHeaderEnv",
                zh ? "鉴权环境变量名" : "Authentication environment variable",
              ],
            ] as const
          ).map(([key, label]) => (
            <label key={key}>
              <span>{label}</span>
              <Input
                aria-label={label}
                value={draft[key] ?? ""}
                required={key !== "authHeaderEnv"}
                type={key === "url" ? "url" : "text"}
                onChange={(event) =>
                  setDraft({ ...draft, [key]: event.target.value })
                }
              />
            </label>
          ))}
          <p>
            {zh
              ? "仅填写服务端环境变量名称。"
              : "Enter the name of a server environment variable."}
          </p>
          <Button
            aria-label={zh ? "注册并同步" : "Register and sync"}
            htmlType="submit"
            loading={pending}
            type="primary"
          >
            {zh ? "注册并同步" : "Register and sync"}
          </Button>
        </form>
      ) : null}
      {servers.length === 0 ? (
        <p>{zh ? "尚未注册 MCP 服务。" : "No MCP servers registered."}</p>
      ) : (
        <ul className="tap-catalog-list tap-tool-list">
          {servers.map((server) => (
            <li key={server.serverId}>
              <article>
                <h3>{server.displayName}</h3>
                <p>{server.url}</p>
                <p>
                  {server.enabled
                    ? zh
                      ? "已启用"
                      : "Enabled"
                    : zh
                      ? "已停用"
                      : "Disabled"}
                  {server.lastSyncedAt ? ` · ${server.lastSyncedAt}` : ""}
                </p>
                {server.lastError ? (
                  <p role="alert">
                    {catalogErrorMessage(
                      new CatalogError(server.lastError),
                      zh,
                    )}{" "}
                    {server.lastError}
                  </p>
                ) : null}
                <ul>
                  {server.tools?.map((tool) => (
                    <li key={tool.toolId}>
                      {tool.displayName} · {tool.effect}
                    </li>
                  ))}
                </ul>
                {server.skippedTools?.map((tool) => (
                  <p key={tool.name}>
                    {tool.name}: {tool.reason}
                  </p>
                ))}
              </article>
              <div className="tap-catalog-actions">
                <Button
                  disabled={pending}
                  onClick={() =>
                    void run(() =>
                      managementCatalog.syncServer(projectId, server.serverId),
                    )
                  }
                >
                  {zh ? "同步" : "Sync"}
                </Button>
                <Button
                  disabled={pending}
                  onClick={() =>
                    void run(() =>
                      managementCatalog.serverStatus(
                        projectId,
                        server.serverId,
                        !server.enabled,
                      ),
                    )
                  }
                >
                  {server.enabled
                    ? zh
                      ? "停用"
                      : "Disable"
                    : zh
                      ? "启用"
                      : "Enable"}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
