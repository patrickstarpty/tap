import { Button, Input } from "antd";
import { useState, type FormEvent } from "react";

export const PROTOTYPE_TOOLS = [
  {
    id: "knowledge.search",
    name: "Knowledge search",
    effect: "read",
    origin: "builtin",
  },
  {
    id: "graph.neighbors",
    name: "Graph neighbors",
    effect: "read",
    origin: "builtin",
  },
  {
    id: "insights.query",
    name: "Test Insights query",
    effect: "read",
    origin: "builtin",
  },
  {
    id: "mcp.claims.lookup",
    name: "Claims lookup",
    effect: "read",
    origin: "mcp",
  },
];
export function ToolsPanel({ zh }: { zh: boolean }) {
  const [servers, setServers] = useState([
    {
      id: "claims",
      name: "Claims tools",
      url: "http://127.0.0.1:8800/mcp",
      enabled: true,
      synced: true,
    },
  ]);
  const [registering, setRegistering] = useState(false);
  const [draft, setDraft] = useState({ slug: "", name: "", url: "", auth: "" });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setServers((current) => [
      ...current,
      {
        id: draft.slug,
        name: draft.name,
        url: draft.url,
        enabled: true,
        synced: true,
      },
    ]);
    setRegistering(false);
  };
  return (
    <section aria-label={zh ? "工具与 MCP" : "Tools and MCP"}>
      <h2>{zh ? "内置工具" : "Built-in tools"}</h2>
      <ul className="tap-catalog-list tap-tool-list">
        {PROTOTYPE_TOOLS.filter((t) => t.origin === "builtin").map((tool) => (
          <li key={tool.id}>
            <article>
              <h3>
                {tool.name} <small>{tool.effect}</small>
              </h3>
              <code>{tool.id}</code>
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
      {registering ? (
        <form onSubmit={submit} className="tap-catalog-server-form">
          {(
            [
              ["slug", zh ? "标识" : "Slug"],
              ["name", zh ? "服务名称" : "Server name"],
              ["url", "URL"],
              [
                "auth",
                zh ? "鉴权环境变量名" : "Authentication environment variable",
              ],
            ] as const
          ).map(([key, label]) => (
            <label key={key}>
              <span>{label}</span>
              <Input
                aria-label={label}
                required={key !== "auth"}
                value={draft[key]}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              />
            </label>
          ))}
          <p>
            {zh
              ? "填写服务端环境变量名称。"
              : "Enter the name of a server environment variable."}
          </p>
          <Button htmlType="submit" type="primary">
            {zh ? "注册并同步" : "Register and sync"}
          </Button>
        </form>
      ) : null}
      <ul className="tap-catalog-list tap-tool-list">
        {servers.map((server) => (
          <li key={server.id}>
            <article>
              <h3>{server.name}</h3>
              <p>{server.url}</p>
              <p>
                {server.enabled
                  ? zh
                    ? "已启用"
                    : "Enabled"
                  : zh
                    ? "已停用"
                    : "Disabled"}{" "}
                ·{" "}
                {server.synced
                  ? zh
                    ? "已同步"
                    : "Synced"
                  : zh
                    ? "待同步"
                    : "Pending sync"}
              </p>
              <p>Claims lookup · read</p>
            </article>
            <div className="tap-catalog-actions">
              <Button
                onClick={() =>
                  setServers((current) =>
                    current.map((s) =>
                      s.id === server.id ? { ...s, synced: true } : s,
                    ),
                  )
                }
              >
                {zh ? "同步" : "Sync"}
              </Button>
              <Button
                onClick={() =>
                  setServers((current) =>
                    current.map((s) =>
                      s.id === server.id ? { ...s, enabled: !s.enabled } : s,
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
    </section>
  );
}
