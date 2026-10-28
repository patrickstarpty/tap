import { Button, Input } from "antd";
import { useId, useMemo, useState, type ReactNode } from "react";

import {
  useMemberHistory,
  useResolveFeedback,
  useTeamMember,
  useTransferOwner,
  useUpdateJobTitle,
  type AgentTeamHistoryEvent,
  type AgentTeamMemberDetail,
  type AgentTeamMemberRow,
  type TeamPeriod,
} from "../../../../../features/knowledge/api/agentTeam";
import { AccessibleDialog } from "../../AccessibleDialog";
import { Avatar } from "./avatar";
import type { WorkspaceCopy } from "../../copy";
import { bucketAxis, fillBuckets, type Bucket } from "./buckets";
import { Sparkline, TrendChart, type SparkTone } from "./charts";
import {
  EMPTY,
  formatCost,
  formatCount,
  formatDate,
  formatDateTime,
  formatDuration,
  memberDisplayName,
  teamLocale,
} from "./format";
import {
  RunValue,
  SatisfactionMeter,
  StatusPill,
  StatusValue,
  ownerName,
  type CellContext,
} from "./memberCells";
import { downsample, memberSeries } from "./series";
import { LoadError, TeamSkeleton } from "./layout";
import { RuntimeBadge, RuntimeMark } from "./RuntimeBadge";
import { handleTablistKey } from "./tabs";

const JOB_TITLE_MAX = 64;
const NOTE_MAX = 500;

export type ProfileTab =
  "basic" | "workload" | "history" | "feedback" | "settings";

export interface AgentProfileDrawerProps {
  projectId: string;
  agentKey: string;
  period: TeamPeriod;
  copy: WorkspaceCopy;
  onClose: () => void;
  onViewConfig: (revisionId: string) => void;
  opener?: HTMLElement | null;
  /** Section to open on, e.g. pending feedback from "Feedback waiting for me". */
  initialTab?: ProfileTab;
}

function length(value: string): number {
  return Array.from(value).length;
}

function errorText(
  error: unknown,
  copy: WorkspaceCopy,
  fallback?: string,
): string {
  const errors = copy.agentTeam.profile.errors;
  const code =
    typeof error === "object" && error !== null && "code" in error
      ? String((error as { code: unknown }).code)
      : "";
  if (code in errors && code !== "generic")
    return errors[code as keyof typeof errors];
  return fallback ?? errors.generic;
}

export function AgentProfileDrawer({
  projectId,
  agentKey,
  period,
  copy,
  onClose,
  onViewConfig,
  opener = null,
  initialTab = "basic",
}: AgentProfileDrawerProps) {
  const text = copy.agentTeam;
  const labels = text.profile;
  const memberQuery = useTeamMember(projectId, agentKey, period);
  const [tab, setTab] = useState<ProfileTab>(initialTab);
  const idPrefix = useId();
  const tabId = (id: ProfileTab) => `${idPrefix}-tab-${id}`;
  const panelId = `${idPrefix}-panel`;
  const view = memberQuery.data;
  const detail = view?.data;
  const row = detail?.row;
  const displayName = row === undefined ? null : memberDisplayName(row, text);

  const tabs: ProfileTab[] = ["basic", "workload", "history", "feedback"];
  if (row?.kind === "custom") tabs.push("settings");
  const active = tabs.includes(tab) ? tab : "basic";

  const ctx: CellContext | null =
    view === undefined
      ? null
      : {
          copy,
          locale: teamLocale(copy),
          timezone: view.timezone,
          runtimeStatus: view.runtimeStatus,
        };

  return (
    <AccessibleDialog
      ariaLabel={displayName ?? text.heading}
      className="tap-agent-profile-drawer"
      onClose={onClose}
      opener={opener}
    >
      <header className="tap-profile-head">
        {row === undefined ? null : (
          <Avatar name={displayName ?? ""} size="lg" />
        )}
        <div className="tap-profile-id">
          <h2>{displayName ?? text.heading}</h2>
          {row === undefined || ctx === null ? null : (
            <>
              <p
                className="tap-profile-title"
                data-empty={row.jobTitle === null ? "true" : undefined}
              >
                {row.jobTitle ?? text.team.noJobTitle}
              </p>
              <p className="tap-profile-meta">
                <StatusPill row={row} ctx={ctx} />
                <span className="tap-profile-owner">
                  <Avatar
                    name={
                      row.kind === "custom"
                        ? (row.ownerActorId ?? labels.noOwner)
                        : ownerName(row, copy)
                    }
                    size="xs"
                  />
                  {text.team.ownerLabel} ·{" "}
                  {row.kind === "custom"
                    ? (row.ownerActorId ?? labels.noOwner)
                    : ownerName(row, copy)}
                </span>
                <span className="tap-profile-kind">
                  {text.team.kinds[row.kind]}
                </span>
              </p>
            </>
          )}
        </div>
        <button
          type="button"
          className="tap-profile-close"
          aria-label={labels.close}
          onClick={onClose}
        >
          <svg aria-hidden="true" width="14" height="14" viewBox="0 0 14 14">
            <path
              d="M3 3 L11 11 M11 3 L3 11"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </header>
      {memberQuery.isPending ? (
        <div className="tap-profile-body">
          <TeamSkeleton copy={copy} kpis={false} cards={2} />
        </div>
      ) : memberQuery.isError ||
        view === undefined ||
        detail === undefined ||
        row === undefined ||
        ctx === null ? (
        <div className="tap-profile-body">
          <LoadError copy={copy} onRetry={() => void memberQuery.refetch()} />
        </div>
      ) : (
        <>
          <div className="tap-profile-freshness">
            <RuntimeBadge
              status={view.runtimeStatus}
              computedAt={view.computedAt}
              timezone={view.timezone}
              copy={copy}
            />
          </div>
          <div
            role="tablist"
            aria-label={labels.tablistLabel}
            className="tap-profile-tabs"
            onKeyDown={(event) =>
              handleTablistKey(event, tabs, active, setTab, tabId)
            }
          >
            {tabs.map((id) => (
              <button
                key={id}
                id={tabId(id)}
                type="button"
                role="tab"
                aria-selected={active === id}
                // Only the active panel is rendered.
                aria-controls={active === id ? panelId : undefined}
                tabIndex={active === id ? 0 : -1}
                onClick={() => setTab(id)}
              >
                {labels.tabs[id]}
                {id === "feedback" && detail.pendingFeedback.length > 0 ? (
                  <span className="tap-profile-tab-count" aria-hidden="true">
                    {detail.pendingFeedback.length}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            id={panelId}
            aria-labelledby={tabId(active)}
            className="tap-profile-body"
          >
            {active === "basic" ? (
              <BasicPanel
                detail={detail}
                copy={copy}
                timezone={view.timezone}
                runtimeStatus={view.runtimeStatus}
                onViewConfig={onViewConfig}
              />
            ) : null}
            {active === "workload" ? (
              <WorkloadPanel
                row={row}
                copy={copy}
                axis={bucketAxis(view.period, view.granularity)}
                ctx={ctx}
              />
            ) : null}
            {active === "history" ? (
              <HistoryPanel
                projectId={projectId}
                row={row}
                copy={copy}
                timezone={view.timezone}
                onViewConfig={onViewConfig}
              />
            ) : null}
            {active === "feedback" ? (
              <FeedbackPanel
                projectId={projectId}
                detail={detail}
                copy={copy}
                timezone={view.timezone}
              />
            ) : null}
            {active === "settings" ? (
              <SettingsPanel projectId={projectId} row={row} copy={copy} />
            ) : null}
          </div>
        </>
      )}
    </AccessibleDialog>
  );
}

function BasicPanel({
  detail,
  copy,
  timezone,
  runtimeStatus,
  onViewConfig,
}: {
  detail: AgentTeamMemberDetail;
  copy: WorkspaceCopy;
  timezone: string;
  runtimeStatus: CellContext["runtimeStatus"];
  onViewConfig: (revisionId: string) => void;
}) {
  const text = copy.agentTeam;
  const labels = text.profile;
  const locale = teamLocale(copy);
  const row = detail.row;
  const revisionId = detail.currentRevisionId;
  const ctx: CellContext = {
    copy,
    locale,
    timezone,
    runtimeStatus,
  };
  const maxServed = Math.max(...detail.served.map((p) => p.runs), 1);
  return (
    <>
      <section className="tap-profile-card">
        <dl className="tap-team-profile-facts">
          <dt>{labels.joinedAt}</dt>
          <dd>{formatDate(row.joinedAt, timezone, locale)}</dd>
          <dt>{text.team.columns.status}</dt>
          <dd>
            <StatusValue row={row} ctx={ctx} />
          </dd>
          <dt>{text.team.ownerLabel}</dt>
          <dd>
            {row.kind === "custom"
              ? (row.ownerActorId ?? labels.noOwner)
              : ownerName(row, copy)}
          </dd>
          <dt>{labels.currentRevision}</dt>
          <dd className="tap-profile-revision">
            <code>{revisionId ?? EMPTY}</code>
            {revisionId === null ? null : (
              <Button
                size="small"
                autoInsertSpace={false}
                onClick={() => onViewConfig(revisionId)}
              >
                {text.viewConfig}
              </Button>
            )}
          </dd>
        </dl>
      </section>
      <section className="tap-profile-card">
        <h3>{labels.servedPeople}</h3>
        <div>
          {runtimeStatus === "unavailable" ? (
            <RunValue value={null} ctx={ctx} />
          ) : detail.served.length === 0 ? (
            <p className="tap-profile-muted">{labels.noServed}</p>
          ) : (
            <ul className="tap-profile-served">
              {detail.served.map((person, index) => (
                <li key={person.actorId}>
                  <span className="tap-profile-served-rank">{index + 1}</span>
                  <Avatar name={person.actorId} size="xs" />
                  <span className="tap-profile-served-name">
                    {person.actorId}
                  </span>
                  <span className="tap-profile-served-bar" aria-hidden="true">
                    <span
                      style={{ width: `${(person.runs / maxServed) * 100}%` }}
                    />
                  </span>
                  <span className="tap-profile-served-runs">
                    {formatCount(person.runs, locale)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </>
  );
}

function WorkloadPanel({
  row,
  copy,
  axis,
  ctx,
}: {
  row: AgentTeamMemberRow;
  copy: WorkspaceCopy;
  axis: Bucket[];
  ctx: CellContext;
}) {
  const text = copy.agentTeam;
  const labels = text.profile;
  const locale = ctx.locale;
  const count = (value: number | null) =>
    value === null ? null : formatCount(value, locale);
  const duration = (value: number | null) =>
    value === null ? null : formatDuration(value, locale, text.durationUnits);
  // Unknown run data is not a flat zero line.
  const unavailable = ctx.runtimeStatus === "unavailable" || row.runs === null;
  const runs = memberSeries(row, axis, "runs");
  const latency = memberSeries(row, axis, "latency");
  const spark = (values: number[]) =>
    unavailable ? null : downsample(values, 14);
  const tiles: {
    id: string;
    label: string;
    value: ReactNode;
    spark: number[] | null;
    tone: SparkTone;
  }[] = [
    {
      id: "runs",
      label: text.metrics.runs,
      value: <RunValue value={count(row.runs)} ctx={ctx} />,
      spark: spark(runs),
      tone: "brand",
    },
    {
      id: "failures",
      label: text.metrics.failures,
      value: <RunValue value={count(row.failures)} ctx={ctx} />,
      spark: spark(memberSeries(row, axis, "failures")),
      tone: "slate",
    },
    {
      id: "p50",
      label: text.team.columns.latencyP50,
      value: <RunValue value={duration(row.latencyP50Ms)} ctx={ctx} />,
      // Average time per run per bucket shows the shape of latency.
      spark: spark(
        latency.map((total, i) => (runs[i] > 0 ? total / runs[i] : 0)),
      ),
      tone: "slate",
    },
    {
      id: "cost",
      label: text.metrics.cost,
      value: (
        <RunValue
          value={row.costUsd === null ? null : formatCost(row.costUsd, locale)}
          ctx={ctx}
        />
      ),
      spark: spark(memberSeries(row, axis, "cost")),
      tone: "slate",
    },
    {
      id: "satisfaction",
      label: text.metrics.satisfaction,
      value: <SatisfactionMeter row={row} ctx={ctx} />,
      spark: null,
      tone: "brand",
    },
  ];
  const runMetrics: [string, ReactNode][] = [
    [
      labels.abstained,
      <RunValue key="ab" value={count(row.abstained)} ctx={ctx} />,
    ],
    [
      labels.canceled,
      <RunValue key="ca" value={count(row.canceled)} ctx={ctx} />,
    ],
    [
      text.team.columns.latencyTotal,
      <RunValue key="lt" value={duration(row.latencyTotalMs)} ctx={ctx} />,
    ],
  ];
  const callMetrics: [string, ReactNode][] = [
    [
      labels.generationCalls,
      <RunValue key="g" value={count(row.generationCalls)} ctx={ctx} />,
    ],
    [
      labels.toolCalls,
      <RunValue key="t" value={count(row.toolCalls)} ctx={ctx} />,
    ],
    [
      labels.callsPerRun,
      <RunValue key="c" value={count(row.callsPerRun)} ctx={ctx} />,
    ],
  ];
  const series = [
    {
      label: labels.trendRuns,
      points: fillBuckets(
        axis,
        row.trend.map((p) => ({ bucketStart: p.bucketStart, value: p.runs })),
      ),
    },
    {
      label: labels.trendFailures,
      points: fillBuckets(
        axis,
        row.trend.map((p) => ({
          bucketStart: p.bucketStart,
          value: p.failures,
        })),
      ),
    },
  ];
  return (
    <>
      <dl className="tap-profile-tiles">
        {tiles.map((tile) => (
          <div key={tile.id} className="tap-profile-tile">
            <dt>{tile.label}</dt>
            <dd>
              <span className="tap-profile-tile-value">{tile.value}</span>
              <span className="tap-profile-tile-spark">
                {tile.spark === null ? (
                  <span
                    className="tap-team-sparkline-empty"
                    aria-hidden="true"
                  />
                ) : (
                  <Sparkline
                    points={tile.spark}
                    label={`${tile.label} ${text.kpi.trendLabel}`}
                    tone={tile.tone}
                    height={28}
                  />
                )}
              </span>
            </dd>
          </div>
        ))}
      </dl>
      <section className="tap-profile-card">
        <h3>{text.team.trendHeading}</h3>
        {unavailable ? (
          <p className="tap-team-trend-unavailable">
            {EMPTY}
            <RuntimeMark status="unavailable" copy={copy} />
          </p>
        ) : (
          <TrendChart
            series={series}
            unit={labels.runsUnit}
            locale={locale}
            hint={text.team.chartHint}
            height={150}
          />
        )}
      </section>
      <section className="tap-profile-card">
        <h3>{labels.primaryMetrics}</h3>
        <dl className="tap-team-profile-metrics">
          {runMetrics.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section className="tap-profile-card">
        <h3>{labels.secondaryMetrics}</h3>
        <dl className="tap-team-profile-metrics">
          {callMetrics.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>
    </>
  );
}

function HistoryPanel({
  projectId,
  row,
  copy,
  timezone,
  onViewConfig,
}: {
  projectId: string;
  row: AgentTeamMemberRow;
  copy: WorkspaceCopy;
  timezone: string;
  onViewConfig: (revisionId: string) => void;
}) {
  const labels = copy.agentTeam.profile;
  const locale = teamLocale(copy);
  // Tapper default is not an authored Agent, so it has no maintenance trail.
  const agentId = row.kind === "tapper_default" ? null : row.agentKey;
  const history = useMemberHistory(projectId, agentId);
  const events: AgentTeamHistoryEvent[] = useMemo(
    () => (history.data?.pages ?? []).flatMap((page) => page.events),
    [history.data],
  );
  if (agentId === null)
    return (
      <p className="tap-team-empty tap-team-empty--quiet">{labels.noHistory}</p>
    );
  if (history.isPending)
    return <TeamSkeleton copy={copy} kpis={false} cards={1} />;
  if (history.isError)
    return <LoadError copy={copy} onRetry={() => void history.refetch()} />;
  return (
    <>
      {events.length === 0 ? (
        <p className="tap-team-empty tap-team-empty--quiet">
          {labels.noHistory}
        </p>
      ) : (
        <ol className="tap-team-history">
          {events.map((event) => (
            <li key={event.eventId} data-kind={event.kind}>
              <span className="tap-team-history-icon" aria-hidden="true">
                <HistoryIcon kind={event.kind} />
              </span>
              <div className="tap-team-history-head">
                <strong>{labels.maintenanceKinds[event.kind]}</strong>
                <span>{event.actorId}</span>
                <time dateTime={event.occurredAt}>
                  {formatDateTime(event.occurredAt, timezone, locale)}
                </time>
              </div>
              {event.note ? <p>{event.note}</p> : null}
              {event.revisionId === null ? null : (
                <Button
                  type="link"
                  size="small"
                  autoInsertSpace={false}
                  className="tap-team-history-link"
                  onClick={() => onViewConfig(event.revisionId as string)}
                >
                  {labels.openRevision}
                </Button>
              )}
            </li>
          ))}
        </ol>
      )}
      {history.hasNextPage ? (
        <Button
          block
          autoInsertSpace={false}
          disabled={history.isFetchingNextPage}
          onClick={() => void history.fetchNextPage()}
        >
          {labels.loadMore}
        </Button>
      ) : null}
    </>
  );
}

function FeedbackPanel({
  projectId,
  detail,
  copy,
  timezone,
}: {
  projectId: string;
  detail: AgentTeamMemberDetail;
  copy: WorkspaceCopy;
  timezone: string;
}) {
  const text = copy.agentTeam;
  const locale = teamLocale(copy);
  const labels = text.profile;
  const resolve = useResolveFeedback(projectId);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  // Per item, so resolving one never disables the others' buttons.
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const items = detail.pendingFeedback;
  const done = async (feedbackId: string) => {
    setPending((prev) => new Set(prev).add(feedbackId));
    try {
      await resolve.mutateAsync({
        feedbackId,
        note: notes[feedbackId]?.trim() || null,
      });
      setErrors((prev) => {
        const rest = { ...prev };
        delete rest[feedbackId];
        return rest;
      });
    } catch (error) {
      setErrors((prev) => ({ ...prev, [feedbackId]: errorText(error, copy) }));
    } finally {
      setPending((prev) => {
        const rest = new Set(prev);
        rest.delete(feedbackId);
        return rest;
      });
    }
  };
  return (
    <>
      <p className="tap-profile-muted">
        {labels.feedbackCount.replace("{count}", String(items.length))}
      </p>
      {items.length === 0 ? (
        <p className="tap-team-empty tap-team-empty--quiet">
          {labels.noFeedback}
        </p>
      ) : (
        <ul className="tap-profile-feedback">
          {items.map((item) => {
            const note = notes[item.feedbackId] ?? "";
            const tooLong = length(note) > NOTE_MAX;
            return (
              <li key={item.feedbackId} className="tap-profile-card">
                <div className="tap-team-feedback-head">
                  <Avatar name={item.actorId} size="sm" />
                  <strong>{item.actorId}</strong>
                  <span
                    className="tap-team-rating"
                    data-rating={item.rating ?? "none"}
                  >
                    {item.rating === "up"
                      ? text.mine.ratingUp
                      : item.rating === "down"
                        ? text.mine.ratingDown
                        : text.mine.ratingNone}
                  </span>
                  <time dateTime={item.updatedAt}>
                    {formatDateTime(item.updatedAt, timezone, locale)}
                  </time>
                </div>
                {item.comment ? (
                  <blockquote className="tap-team-feedback-quote">
                    {item.comment}
                  </blockquote>
                ) : null}
                <label className="tap-profile-field">
                  <span>{labels.resolutionNote}</span>
                  <Input.TextArea
                    rows={3}
                    status={tooLong ? "error" : undefined}
                    value={note}
                    onChange={(event) =>
                      setNotes((prev) => ({
                        ...prev,
                        [item.feedbackId]: event.target.value,
                      }))
                    }
                  />
                </label>
                {tooLong ? (
                  <p role="alert" className="tap-profile-error">
                    {labels.noteTooLong}
                  </p>
                ) : null}
                {errors[item.feedbackId] ? (
                  <p role="alert" className="tap-profile-error">
                    {errors[item.feedbackId]}
                  </p>
                ) : null}
                <div className="tap-profile-actions">
                  <Button
                    autoInsertSpace={false}
                    disabled={tooLong || pending.has(item.feedbackId)}
                    onClick={() => void done(item.feedbackId)}
                  >
                    {text.markResolved}
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

function SettingsPanel({
  projectId,
  row,
  copy,
}: {
  projectId: string;
  row: AgentTeamMemberRow;
  copy: WorkspaceCopy;
}) {
  const text = copy.agentTeam;
  const labels = text.profile;
  const updateTitle = useUpdateJobTitle(projectId);
  const transfer = useTransferOwner(projectId);
  const [title, setTitle] = useState(row.jobTitle ?? "");
  const [owner, setOwner] = useState("");
  const [titleError, setTitleError] = useState<string | null>(null);
  const [ownerError, setOwnerError] = useState<string | null>(null);
  // Confirmation of the last successful write; cleared by the next edit.
  const [titleSaved, setTitleSaved] = useState(false);
  const [transferredTo, setTransferredTo] = useState<string | null>(null);
  const titleTooLong = length(title) > JOB_TITLE_MAX;
  const saveTitle = async () => {
    setTitleError(null);
    setTitleSaved(false);
    try {
      await updateTitle.mutateAsync({
        agentId: row.agentKey,
        jobTitle: title.trim() === "" ? null : title.trim(),
      });
      setTitleSaved(true);
    } catch (error) {
      setTitleError(errorText(error, copy));
    }
  };
  const transferOwner = async () => {
    const next = owner.trim();
    setOwnerError(null);
    setTransferredTo(null);
    try {
      await transfer.mutateAsync({
        agentId: row.agentKey,
        newOwnerActorId: next,
      });
      setOwner("");
      setTransferredTo(next);
    } catch (error) {
      setOwnerError(errorText(error, copy, labels.transferFailed));
    }
  };
  return (
    <>
      <section className="tap-profile-card">
        <label className="tap-profile-field">
          <span>{labels.jobTitleLabel}</span>
          <Input
            value={title}
            status={titleTooLong ? "error" : undefined}
            onChange={(event) => {
              setTitle(event.target.value);
              setTitleSaved(false);
            }}
          />
        </label>
        <p className="tap-profile-counter" aria-hidden="true">
          {length(title)}/{JOB_TITLE_MAX}
        </p>
        {titleTooLong ? (
          <p role="alert" className="tap-profile-error">
            {labels.jobTitleTooLong}
          </p>
        ) : null}
        {titleError ? (
          <p role="alert" className="tap-profile-error">
            {titleError}
          </p>
        ) : null}
        {titleSaved ? (
          <p role="status" className="tap-profile-success">
            {labels.saved}
          </p>
        ) : null}
        <div className="tap-profile-actions">
          <Button
            type="primary"
            autoInsertSpace={false}
            disabled={titleTooLong || updateTitle.isPending}
            onClick={() => void saveTitle()}
          >
            {labels.save}
          </Button>
        </div>
      </section>
      <section className="tap-profile-card">
        <dl className="tap-team-profile-facts">
          <dt>{labels.currentOwner}</dt>
          <dd>{row.ownerActorId ?? labels.noOwner}</dd>
        </dl>
        <label className="tap-profile-field">
          <span>{labels.newOwnerLabel}</span>
          <Input
            value={owner}
            onChange={(event) => {
              setOwner(event.target.value);
              setTransferredTo(null);
            }}
          />
        </label>
        {ownerError ? (
          <p role="alert" className="tap-profile-error">
            {ownerError}
          </p>
        ) : null}
        {transferredTo === null ? null : (
          <p role="status" className="tap-profile-success">
            {labels.transferred.replace("{owner}", transferredTo)}
          </p>
        )}
        <div className="tap-profile-actions">
          <Button
            autoInsertSpace={false}
            disabled={owner.trim() === "" || transfer.isPending}
            onClick={() => void transferOwner()}
          >
            {text.transferOwner}
          </Button>
        </div>
      </section>
    </>
  );
}

/** One glyph per maintenance kind, so the timeline scans at a glance. */
function HistoryIcon({ kind }: { kind: AgentTeamHistoryEvent["kind"] }) {
  const paths: Record<AgentTeamHistoryEvent["kind"], string> = {
    revised: "M9.5 2.5l2 2L5 11H3V9l6.5-6.5z",
    skill_upgraded: "M7 11.5V3M3.5 6.5L7 3l3.5 3.5M3 12h8",
    disabled: "M7 12.5A5.5 5.5 0 1 0 7 1.5a5.5 5.5 0 0 0 0 11zM3.2 3.2l7.6 7.6",
    enabled:
      "M7 12.5A5.5 5.5 0 1 0 7 1.5a5.5 5.5 0 0 0 0 11zM4.5 7.2l1.7 1.7 3.3-3.4",
    feedback_resolved: "M2.5 3.5h9v5.5h-5L4 11V9H2.5zM5 6.4l1.4 1.3 2.6-2.6",
    owner_transferred: "M2.5 5h8M8.5 3l2 2-2 2M11.5 9h-8M5.5 7l-2 2 2 2",
  };
  return (
    <svg width="14" height="14" viewBox="0 0 14 14">
      <path
        d={paths[kind]}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
