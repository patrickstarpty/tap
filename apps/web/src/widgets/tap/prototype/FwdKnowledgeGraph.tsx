import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent,
} from "react";
import {
  AimOutlined,
  CloseOutlined,
  FullscreenOutlined,
  MinusOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import {
  FWD_GROUPS,
  FWD_KNOWLEDGE,
  FWD_REVIEW_DATE,
  FWD_SOURCES,
  getFwdNeighborhood,
  type FwdGroup,
  type FwdNode,
} from "./fwdKnowledge";
import type { LibrarySource } from "./model";
import { PROTOTYPE_COPY } from "./copy";
import "./FwdKnowledgeGraph.css";

const W = 1520,
  H = 1380;
const nodeMap = new Map(FWD_KNOWLEDGE.nodes.map((n) => [n.id, n]));
const groupMap = new Map(FWD_GROUPS.map((g) => [g.id, g]));
const positions = new Map<string, { x: number; y: number }>();
for (const group of FWD_GROUPS) {
  const members = FWD_KNOWLEDGE.nodes.filter((n) => n.group === group.id);
  const hubs = members.filter((n) => n.hub),
    leaves = members.filter((n) => !n.hub);
  hubs.forEach((n, i) =>
    positions.set(n.id, {
      x:
        group.x +
        (hubs.length === 1 ? 0 : Math.cos(i * 2.399) * Math.sqrt(i) * 44),
      y:
        group.y +
        (hubs.length === 1 ? 0 : Math.sin(i * 2.399) * Math.sqrt(i) * 32),
    }),
  );
  leaves.forEach((n, i) => {
    const radius = 62 + Math.sqrt((i + 1) / leaves.length) * 148;
    positions.set(n.id, {
      x: group.x + Math.cos(i * 2.399963) * radius,
      y: group.y + Math.sin(i * 2.399963) * radius * 0.68,
    });
  });
}
const kindLabels: Record<FwdNode["kind"], [string, string]> = {
  product: ["Product / series", "产品／系列"],
  topic: ["Topic", "主题"],
  process: ["Business process", "业务流程"],
  rule: ["Demo rule", "演示规则"],
  system: ["System module", "系统模块"],
  code: ["Code file", "代码文件"],
  test: ["Test case", "测试用例"],
  automation: ["Automation", "自动化"],
  execution: ["Demo execution", "模拟执行"],
  defect: ["Demo defect", "模拟缺陷"],
};
const short = (s: string, max = 31) =>
  s.length > max ? s.slice(0, max - 1) + "…" : s;

export function FwdKnowledgeGraph({
  chinese,
  query,
  sources,
}: {
  chinese: boolean;
  query: string;
  sources: readonly LibrarySource[];
}) {
  const t = (en: string, zh: string) => (chinese ? zh : en);
  const libraryCopy = PROTOTYPE_COPY[chinese ? "zh" : "en"].library;
  const workspace = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const detailsRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (detailsRef.current) detailsRef.current.scrollTop = 0;
  }, [selected]);
  const [hovered, setHovered] = useState<string | null>(null);
  const [group, setGroup] = useState<FwdGroup | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [hops, setHops] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [fullscreenError, setFullscreenError] = useState(false);
  const drag = useRef<{
    x: number;
    y: number;
    px: number;
    py: number;
    scale: number;
    pointer: number;
  } | null>(null);
  useEffect(() => {
    if (window.matchMedia("(max-width: 640px)").matches) {
      const canvas =
        workspace.current?.querySelector<HTMLElement>(".tap-fwd-canvas");
      if (canvas)
        canvas.scrollLeft = (canvas.scrollWidth - canvas.clientWidth) / 2;
    }
  }, [focus, group]);
  const q = query.trim().toLowerCase();
  const permitted = new Set(sources.map((s) => s.id));
  const baseNodes = FWD_KNOWLEDGE.nodes.filter(
    (n) => !n.path || permitted.has(`fwd-${n.id}`),
  );
  const searchResults = baseNodes
    .filter((n) => !group || n.group === group)
    .filter((n) =>
      [n.label, n.path, n.content, kindLabels[n.kind].join(" ")].some((s) =>
        s?.toLowerCase().includes(q),
      ),
    );
  const focusIds = useMemo(
    () => (focus ? getFwdNeighborhood(FWD_KNOWLEDGE, focus, hops) : null),
    [focus, hops],
  );
  const visible = baseNodes.filter(
    (n) => (!group || n.group === group) && (!focusIds || focusIds.has(n.id)),
  );
  const visibleIds = new Set(visible.map((n) => n.id));
  const links = FWD_KNOWLEDGE.edges.filter(
    (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
  );
  const selectedNode = selected ? nodeMap.get(selected) : undefined;
  const active = hovered ?? selected;
  const near = active ? getFwdNeighborhood(FWD_KNOWLEDGE, active, 1) : null;
  const related = FWD_KNOWLEDGE.edges.filter(
    (e) => e.source === selected || e.target === selected,
  );
  const pointMap = useMemo(() => {
    if (!focus) return positions;
    const result = new Map<string, { x: number; y: number }>();
    result.set(focus, { x: W / 2, y: H / 2 });
    const others = FWD_KNOWLEDGE.nodes.filter(
      (n) => focusIds?.has(n.id) && n.id !== focus,
    );
    others.forEach((n, i) => {
      const radius = 170 + Math.sqrt((i + 1) / others.length) * 340;
      result.set(n.id, {
        x: W / 2 + Math.cos(i * 2.399963) * radius,
        y: H / 2 + Math.sin(i * 2.399963) * radius * 0.85,
      });
    });
    return result;
  }, [focus, focusIds]);
  let box = { x: -30, y: -50, width: W + 60, height: H + 30 };
  if ((group || focus) && visible.length) {
    const ps = visible.map((n) => pointMap.get(n.id)!);
    const xs = ps.map((p) => p.x),
      ys = ps.map((p) => p.y);
    box = {
      x: Math.min(...xs) - 130,
      y: Math.min(...ys) - 100,
      width: Math.max(...xs) - Math.min(...xs) + 260,
      height: Math.max(...ys) - Math.min(...ys) + 200,
    };
  }
  const showAllNames =
    Boolean(group && visible.length < 16) ||
    Boolean(focus && visible.length < 24);
  const reset = () => {
    setSelected(null);
    setHovered(null);
    setGroup(null);
    setFocus(null);
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };
  const select = (id: string) => {
    setSelected(id);
    setHovered(null);
    if (group && nodeMap.get(id)?.group !== group) setGroup(null);
    if (focus) setFocus(id);
    setPan({ x: 0, y: 0 });
  };
  const selectSearch = (id: string) => {
    setGroup(null);
    setSelected(id);
    setFocus(id);
    setHops(1);
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };
  const startPan = (e: PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0 || (e.target as Element).closest('[role="button"]'))
      return;
    const rect = e.currentTarget.getBoundingClientRect();
    const scale =
      Math.min(rect.width / box.width, rect.height / box.height) * zoom;
    drag.current = {
      x: e.clientX,
      y: e.clientY,
      px: pan.x,
      py: pan.y,
      scale,
      pointer: e.pointerId,
    };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const movePan = (e: PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (d && d.pointer === e.pointerId)
      setPan({
        x: d.px + (e.clientX - d.x) / d.scale,
        y: d.py + (e.clientY - d.y) / d.scale,
      });
  };
  const endPan = (e: PointerEvent<SVGSVGElement>) => {
    if (drag.current?.pointer === e.pointerId) {
      drag.current = null;
      e.currentTarget.releasePointerCapture?.(e.pointerId);
    }
  };
  const download = selectedNode
    ? FWD_SOURCES.find((s) => s.id === `fwd-${selectedNode.id}`)
    : undefined;
  const publicCount = FWD_KNOWLEDGE.nodes.filter(
    (n) => n.provenance === "public",
  ).length;
  return (
    <div ref={workspace} className="tap-fwd-knowledge">
      <div className="tap-fwd-workspace" data-details={Boolean(selectedNode)}>
        <aside className="tap-fwd-nav">
          <button type="button" className="tap-fwd-overview" onClick={reset}>
            <AimOutlined aria-hidden="true" /> {t("Overview", "总览")}
          </button>
          {q || group ? (
            <section
              role="region"
              aria-label={
                q ? libraryCopy.searchResults : libraryCopy.communities
              }
            >
              <h3>
                {q ? libraryCopy.searchResults : libraryCopy.communities}{" "}
                <small>{searchResults.length}</small>
              </h3>
              <ul>
                {searchResults.map((n) => (
                  <li key={n.id}>
                    <button
                      type="button"
                      onClick={() => selectSearch(n.id)}
                      aria-label={n.label}
                      aria-pressed={selected === n.id}
                    >
                      <strong>{n.label}</strong>
                      <small>{kindLabels[n.kind][chinese ? 1 : 0]}</small>
                    </button>
                  </li>
                ))}
              </ul>
              {!searchResults.length && (
                <p>{t("No matching knowledge", "没有匹配的知识")}</p>
              )}
            </section>
          ) : (
            <>
              <h3>{libraryCopy.communities}</h3>
              <div className="tap-fwd-layers">
                {FWD_GROUPS.map((g) => (
                  <button
                    key={g.id}
                    type="button"
                    aria-pressed={group === g.id}
                    onClick={() => {
                      reset();
                      setGroup(g.id);
                    }}
                  >
                    <i style={{ background: g.color }} />
                    <span>{chinese ? g.zh : g.en}</span>
                    <small>
                      {baseNodes.filter((n) => n.group === g.id).length}
                    </small>
                  </button>
                ))}
              </div>
              <div className="tap-fwd-source-key">
                <h3>{libraryCopy.provenance}</h3>
                <p>
                  <i /> {t("Public references", "公开资料")}{" "}
                  <strong>{publicCount}</strong>
                </p>
                <p>
                  <i data-demo /> {t("Demo models", "演示建模")}{" "}
                  <strong>{FWD_KNOWLEDGE.nodes.length - publicCount}</strong>
                </p>
                <small>
                  {t("Reviewed", "核对日期")} {FWD_REVIEW_DATE}
                  <br />
                  {t(
                    "Directory entries include series and online versions; not a count of unique contracts.",
                    "目录含产品系列及网上版本，不等同于独立保单产品数。",
                  )}
                </small>
              </div>
            </>
          )}
        </aside>
        <figure className="tap-fwd-figure">
          <div className="tap-fwd-tools">
            <span>
              {visible.length} {t("visible", "个可见节点")}
            </span>
            <div>
              <button
                type="button"
                title={t("Zoom out", "缩小")}
                aria-label={t("Zoom out", "缩小")}
                disabled={zoom <= 0.5}
                onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}
              >
                <MinusOutlined aria-hidden="true" />
              </button>
              <output aria-label={t("Zoom level", "缩放比例")}>
                {Math.round(zoom * 100)}%
              </output>
              <button
                type="button"
                title={t("Zoom in", "放大")}
                aria-label={t("Zoom in", "放大")}
                disabled={zoom >= 4}
                onClick={() => setZoom((z) => Math.min(4, z + 0.25))}
              >
                <PlusOutlined aria-hidden="true" />
              </button>
              <button
                type="button"
                aria-label={t("Fit graph", "适应画布")}
                onClick={() => {
                  setZoom(1);
                  setPan({ x: 0, y: 0 });
                }}
              >
                <AimOutlined aria-hidden="true" />
              </button>
              <button
                type="button"
                aria-label={t("Toggle fullscreen", "切换全屏")}
                onClick={async () => {
                  try {
                    if (document.fullscreenElement === workspace.current)
                      await document.exitFullscreen();
                    else await workspace.current?.requestFullscreen();
                    setFullscreenError(false);
                  } catch {
                    setFullscreenError(true);
                  }
                }}
              >
                <FullscreenOutlined aria-hidden="true" />
              </button>
            </div>
          </div>
          {fullscreenError && (
            <p role="status">
              {t(
                "Fullscreen is unavailable in this browser.",
                "当前浏览器无法进入全屏。",
              )}
            </p>
          )}
          {focus && (
            <div className="tap-fwd-focus-bar">
              <span>
                {t("Connections around", "聚焦节点")}{" "}
                <strong>{short(nodeMap.get(focus)!.label)}</strong>
              </span>
              <label>
                {t("Depth", "层数")}{" "}
                <select
                  aria-label={t("Connection depth", "关联深度")}
                  value={hops}
                  onChange={(e) => setHops(Number(e.target.value))}
                >
                  <option value={1}>1</option>
                  <option value={2}>2</option>
                </select>
              </label>
            </div>
          )}
          <div className="tap-fwd-canvas">
            <svg
              role="group"
              aria-label="FWD HK knowledge graph"
              viewBox={`${box.x} ${box.y} ${box.width} ${box.height}`}
              onPointerDown={startPan}
              onPointerMove={movePan}
              onPointerUp={endPan}
              onPointerCancel={endPan}
            >
              <g
                transform={`translate(${box.x + box.width / 2} ${box.y + box.height / 2}) scale(${zoom}) translate(${-box.x - box.width / 2 + pan.x} ${-box.y - box.height / 2 + pan.y})`}
              >
                {!focus &&
                  FWD_GROUPS.filter(
                    (g) =>
                      (!group || g.id === group) &&
                      visible.some((n) => n.group === g.id),
                  ).map((g) => (
                    <g
                      key={g.id}
                      className="tap-fwd-cluster"
                      style={{ "--cluster-color": g.color } as CSSProperties}
                    >
                      <ellipse cx={g.x} cy={g.y} rx={235} ry={170} />
                      <text x={g.x} y={g.y - 184} textAnchor="middle">
                        {chinese ? g.zh : g.en}
                      </text>
                    </g>
                  ))}
                <g aria-hidden="true">
                  {links.map((e, i) => {
                    const a = pointMap.get(e.source)!,
                      b = pointMap.get(e.target)!;
                    const linked = e.source === active || e.target === active;
                    return (
                      <g key={e.id}>
                        <path
                          className="tap-fwd-edge"
                          data-active={linked}
                          data-muted={Boolean(active) && !linked}
                          data-public={e.provenance === "public"}
                          stroke={
                            groupMap.get(nodeMap.get(e.target)!.group)!.color
                          }
                          d={`M ${a.x} ${a.y} Q ${(a.x + b.x) / 2 + (i % 2 ? 20 : -20)} ${(a.y + b.y) / 2 - 30} ${b.x} ${b.y}`}
                        />
                        {focus && linked && visible.length < 20 && (
                          <text
                            className="tap-fwd-relation-label"
                            x={(a.x + b.x) / 2}
                            y={(a.y + b.y) / 2 - 22}
                            textAnchor="middle"
                          >
                            {e.relation}
                          </text>
                        )}
                      </g>
                    );
                  })}
                </g>
                {visible.map((n) => {
                  const p = pointMap.get(n.id)!;
                  const chosen = n.id === selected,
                    activeNode = n.id === active;
                  const color = groupMap.get(n.group)!.color;
                  const radius = focus
                    ? n.id === focus
                      ? 19
                      : 12
                    : n.hub
                      ? 14
                      : n.kind === "product"
                        ? 9
                        : 7;
                  const label =
                    showAllNames ||
                    activeNode ||
                    Boolean(focus && near?.has(n.id) && visible.length < 15);
                  return (
                    <g
                      key={n.id}
                      className="tap-fwd-node"
                      role="button"
                      tabIndex={0}
                      aria-label={n.label}
                      aria-pressed={chosen}
                      data-dim={Boolean(active) && !near?.has(n.id)}
                      data-selected={chosen}
                      data-public={n.provenance === "public"}
                      onClick={() => select(n.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          select(n.id);
                        }
                      }}
                      onMouseEnter={() => setHovered(n.id)}
                      onMouseLeave={() => setHovered(null)}
                      onFocus={() => setHovered(n.id)}
                      onBlur={() => setHovered(null)}
                      style={{ "--node-color": color } as CSSProperties}
                    >
                      <circle
                        className="tap-fwd-hit"
                        cx={p.x}
                        cy={p.y}
                        r={radius + 10}
                      />
                      <circle
                        className="tap-fwd-halo"
                        cx={p.x}
                        cy={p.y}
                        r={radius + 7}
                      />
                      {n.kind === "code" || n.kind === "automation" ? (
                        <rect
                          className="tap-fwd-core"
                          x={p.x - radius}
                          y={p.y - radius}
                          width={radius * 2}
                          height={radius * 2}
                          rx={3}
                        />
                      ) : (
                        <circle
                          className="tap-fwd-core"
                          cx={p.x}
                          cy={p.y}
                          r={radius}
                        />
                      )}
                      {label && (
                        <text
                          x={p.x}
                          y={p.y + radius + 23}
                          textAnchor="middle"
                          className="tap-fwd-node-label"
                        >
                          {short(n.label, activeNode ? 48 : 29)}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            </svg>
          </div>
          <figcaption>
            {t(
              "Select a node to inspect its relationships. Drag to pan; use + to explore detail. Runs and findings are illustrative, not executed.",
              "点击节点查看关联，拖动画布或放大探索细节。执行结果与缺陷为模拟记录，未实际执行。",
            )}
          </figcaption>
        </figure>
        {selectedNode && (
          <aside
            ref={detailsRef}
            className="tap-fwd-details"
            role="region"
            aria-label={libraryCopy.nodeDetails}
          >
            <header>
              <h3>{libraryCopy.nodeDetails}</h3>
              <button
                type="button"
                aria-label={t("Close node details", "关闭节点详情")}
                onClick={() => {
                  setSelected(null);
                  setHovered(null);
                }}
              >
                <CloseOutlined aria-hidden="true" />
              </button>
            </header>
            <span className="tap-fwd-kind">
              {kindLabels[selectedNode.kind][chinese ? 1 : 0]}
            </span>
            <h2>{selectedNode.label}</h2>
            <span
              className="tap-fwd-badge"
              data-public={selectedNode.provenance === "public"}
            >
              {selectedNode.provenance === "public"
                ? t("Public source", "公开资料")
                : t("Demo model", "演示建模")}
            </span>
            <p className="tap-fwd-detail-summary">
              {selectedNode.kind === "code" ||
              selectedNode.kind === "automation"
                ? t(
                    "Illustrative source file linked to business requirements and tests. This is not FWD's internal codebase.",
                    "与业务和测试关联的示例文件，并非 FWD 内部代码库。",
                  )
                : selectedNode.content
                    .replace(/^# [^\n]+\n+/, "")
                    .split("\n\n")[0]}
            </p>
            {selectedNode.path && (
              <code className="tap-fwd-path">{selectedNode.path}</code>
            )}
            <div className="tap-fwd-detail-actions">
              <button
                type="button"
                onClick={() => {
                  setFocus(selectedNode.id);
                  setGroup(null);
                  setHops(1);
                  setZoom(1);
                  setPan({ x: 0, y: 0 });
                }}
              >
                {t("Focus connections", "聚焦关联")}
              </button>
              {selectedNode.sourceUrl && (
                <a
                  href={selectedNode.sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("Official source", "官网来源")} ↗
                </a>
              )}
              {download && (
                <a href={download.downloadUrl} download={download.name}>
                  {t("Download file", "下载文件")}
                </a>
              )}
            </div>
            <details className="tap-fwd-content">
              <summary>{t("Knowledge content", "知识内容")}</summary>
              <pre>{selectedNode.content}</pre>
            </details>
            <h3>
              {t("Relationships", "关联关系")} <small>{related.length}</small>
            </h3>
            <ul className="tap-fwd-relations">
              {related.map((e) => {
                const outgoing = e.source === selected;
                const other = nodeMap.get(outgoing ? e.target : e.source)!;
                return (
                  <li key={e.id}>
                    <span>
                      {outgoing ? "→" : "←"} {e.relation}{" "}
                      <small>
                        {e.provenance === "public"
                          ? t("public", "公开")
                          : t("demo", "演示")}
                      </small>
                    </span>
                    <button type="button" onClick={() => select(other.id)}>
                      {other.label}
                    </button>
                  </li>
                );
              })}
            </ul>
          </aside>
        )}
      </div>
      <p className="tap-fwd-footnote">
        <a
          className="tap-fwd-pack"
          href="/prototype-files/fwd-hk-knowledge-demo.zip"
          download="fwd-hk-knowledge-demo.zip"
        >
          {t("Download knowledge pack", "下载知识包")}
        </a>
        {" · "}

        {t(
          "Scope: FWD HK public product directory and online references, checked 6 Sep 2026. Series may contain multiple versions. Availability is directory-listed, not independently confirmed for every channel. System architecture, code, rules, tests and traces are curated demo models.",
          "范围：2026-09-06 核对的 FWD HK 官网目录及网上产品资料，系列可能包含多个版本；目录列示不代表已逐一确认各渠道在售状态。系统架构、代码、规则、测试和执行链路均为人工编排的演示模型。",
        )}
      </p>
    </div>
  );
}
