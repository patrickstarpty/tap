import { useMemo, useState, type ReactNode } from "react";

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
import type { WorkspaceCopy } from "../../copy";
import { bucketAxis, fillBuckets, type Bucket } from "./buckets";
import { TrendChart } from "./charts";
import {
  EMPTY,
  formatCost,
  formatCount,
  formatDate,
  formatDateTime,
  formatDuration,
  teamLocale,
} from "./format";
import {
  RunValue,
  SatisfactionValue,
  StatusValue,
  ownerName,
  type CellContext,
} from "./memberCells";
import { RuntimeBadge } from "./RuntimeBadge";

const JOB_TITLE_MAX = 64;
const NOTE_MAX = 500;

type ProfileTab = "basic" | "workload" | "history" | "feedback" | "settings";

export interface AgentProfileDrawerProps {
  projectId: string;
  agentKey: string;
  period: TeamPeriod;
  copy: WorkspaceCopy;
  onClose: () => void;
  onViewConfig: (revisionId: string) => void;
  opener?: HTMLElement | null;
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
}: AgentProfileDrawerProps) {
  const text = copy.agentTeam;
  const labels = text.profile;
  const memberQuery = useTeamMember(projectId, agentKey, period);
  const [tab, setTab] = useState<ProfileTab>("basic");
  const view = memberQuery.data;
  const detail = view?.data;
  const row = detail?.row;

  const tabs: ProfileTab[] = ["basic", "workload", "history", "feedback"];
  if (row?.kind === "custom") tabs.push("settings");
  const active = tabs.includes(tab) ? tab : "basic";

  return (
    <AccessibleDialog
      ariaLabel={row?.name ?? text.heading}
      className="tap-agent-profile-drawer tap-catalog-dialog"
      onClose={onClose}
      opener={opener}
    >
      <header>
        <h2>{row?.name ?? text.heading}</h2>
        <button type="button" onClick={onClose}>
          {labels.close}
        </button>
      </header>
      {memberQuery.isPending ? (
        <p role="status">{text.loading}</p>
      ) : memberQuery.isError ||
        view === undefined ||
        detail === undefined ||
        row === undefined ? (
        <div role="alert">
          <p>{text.loadFailed}</p>
          <button type="button" onClick={() => void memberQuery.refetch()}>
            {text.retry}
          </button>
        </div>
      ) : (
        <>
          <RuntimeBadge
            status={view.runtimeStatus}
            computedAt={view.computedAt}
            timezone={view.timezone}
            copy={copy}
          />
          <div role="tablist" aria-label={labels.tablistLabel}>
            {tabs.map((id) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={active === id}
                onClick={() => setTab(id)}
              >
                {labels.tabs[id]}
              </button>
            ))}
          </div>
          <div role="tabpanel">
            {active === "basic" ? (
              <BasicPanel
                detail={detail}
                copy={copy}
                timezone={view.timezone}
                onViewConfig={onViewConfig}
              />
            ) : null}
            {active === "workload" ? (
              <WorkloadPanel
                row={row}
                copy={copy}
                axis={bucketAxis(view.period, view.granularity)}
                ctx={{
                  copy,
                  locale: teamLocale(copy),
                  timezone: view.timezone,
                  runtimeStatus: view.runtimeStatus,
                }}
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
  onViewConfig,
}: {
  detail: AgentTeamMemberDetail;
  copy: WorkspaceCopy;
  timezone: string;
  onViewConfig: (revisionId: string) => void;
}) {
  const text = copy.agentTeam;
  const labels = text.profile;
  const locale = teamLocale(copy);
  const row = detail.row;
  const revisionId = detail.currentRevisionId;
  return (
    <>
      <dl>
        <dt>{text.team.columns.jobTitle}</dt>
        <dd>{row.jobTitle ?? text.team.noJobTitle}</dd>
        <dt>{labels.joinedAt}</dt>
        <dd>{formatDate(row.joinedAt, timezone, locale)}</dd>
        <dt>{text.team.ownerLabel}</dt>
        <dd>
          {row.kind === "custom"
            ? (row.ownerActorId ?? labels.noOwner)
            : ownerName(row, copy)}
        </dd>
        <dt>{labels.currentRevision}</dt>
        <dd>{revisionId ?? EMPTY}</dd>
        <dt>{labels.servedPeople}</dt>
        <dd>
          {detail.served.length === 0 ? (
            labels.noServed
          ) : (
            <ul>
              {detail.served.map((person) => (
                <li key={person.actorId}>
                  {person.actorId} · {formatCount(person.runs, locale)}
                </li>
              ))}
            </ul>
          )}
        </dd>
      </dl>
      {revisionId === null ? null : (
        <button type="button" onClick={() => onViewConfig(revisionId)}>
          {text.viewConfig}
        </button>
      )}
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
  const primary: [string, ReactNode][] = [
    [
      text.metrics.runs,
      <RunValue key="runs" value={count(row.runs)} ctx={ctx} />,
    ],
    [
      text.metrics.failures,
      <RunValue key="failures" value={count(row.failures)} ctx={ctx} />,
    ],
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
      <RunValue
        key="lt"
        value={
          row.latencyTotalMs === null
            ? null
            : formatDuration(row.latencyTotalMs, locale)
        }
        ctx={ctx}
      />,
    ],
    [
      text.team.columns.latencyP50,
      <RunValue
        key="lp"
        value={
          row.latencyP50Ms === null
            ? null
            : formatDuration(row.latencyP50Ms, locale)
        }
        ctx={ctx}
      />,
    ],
    [
      text.metrics.cost,
      <RunValue
        key="cost"
        value={row.costUsd === null ? null : formatCost(row.costUsd, locale)}
        ctx={ctx}
      />,
    ],
    [
      text.metrics.satisfaction,
      <SatisfactionValue key="sat" row={row} ctx={ctx} />,
    ],
    [text.team.columns.status, <StatusValue key="st" row={row} ctx={ctx} />],
  ];
  const secondary: [string, ReactNode][] = [
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
      <TrendChart series={series} unit={labels.runsUnit} locale={locale} />
      <dl className="tap-team-profile-metrics">
        {primary.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <h3>{labels.secondaryMetrics}</h3>
      <dl className="tap-team-profile-metrics">
        {secondary.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
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
  if (agentId === null) return <p>{labels.noHistory}</p>;
  if (history.isPending) return <p role="status">{copy.agentTeam.loading}</p>;
  if (history.isError)
    return (
      <div role="alert">
        <p>{copy.agentTeam.loadFailed}</p>
        <button type="button" onClick={() => void history.refetch()}>
          {copy.agentTeam.retry}
        </button>
      </div>
    );
  return (
    <>
      {events.length === 0 ? (
        <p>{labels.noHistory}</p>
      ) : (
        <ol className="tap-team-history">
          {events.map((event) => (
            <li key={event.eventId}>
              <time dateTime={event.occurredAt}>
                {formatDateTime(event.occurredAt, timezone, locale)}
              </time>{" "}
              <strong>{labels.maintenanceKinds[event.kind]}</strong>{" "}
              <span>{event.actorId}</span>
              {event.note ? <p>{event.note}</p> : null}
              {event.revisionId === null ? null : (
                <button
                  type="button"
                  onClick={() => onViewConfig(event.revisionId as string)}
                >
                  {labels.openRevision}
                </button>
              )}
            </li>
          ))}
        </ol>
      )}
      {history.hasNextPage ? (
        <button
          type="button"
          disabled={history.isFetchingNextPage}
          onClick={() => void history.fetchNextPage()}
        >
          {labels.loadMore}
        </button>
      ) : null}
    </>
  );
}

function FeedbackPanel({
  projectId,
  detail,
  copy,
}: {
  projectId: string;
  detail: AgentTeamMemberDetail;
  copy: WorkspaceCopy;
}) {
  const text = copy.agentTeam;
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
      <p>{labels.feedbackCount.replace("{count}", String(items.length))}</p>
      {items.length === 0 ? (
        <p>{labels.noFeedback}</p>
      ) : (
        <ul className="tap-team-feedback">
          {items.map((item) => {
            const note = notes[item.feedbackId] ?? "";
            const tooLong = length(note) > NOTE_MAX;
            return (
              <li key={item.feedbackId}>
                <p>
                  {item.actorId}
                  {` · ${
                    item.rating === "up"
                      ? text.mine.ratingUp
                      : item.rating === "down"
                        ? text.mine.ratingDown
                        : text.mine.ratingNone
                  }`}
                </p>
                {item.comment ? <p>{item.comment}</p> : null}
                <label>
                  {labels.resolutionNote}
                  <textarea
                    value={note}
                    onChange={(event) =>
                      setNotes((prev) => ({
                        ...prev,
                        [item.feedbackId]: event.target.value,
                      }))
                    }
                  />
                </label>
                {tooLong ? <p role="alert">{labels.noteTooLong}</p> : null}
                {errors[item.feedbackId] ? (
                  <p role="alert">{errors[item.feedbackId]}</p>
                ) : null}
                <button
                  type="button"
                  disabled={tooLong || pending.has(item.feedbackId)}
                  onClick={() => void done(item.feedbackId)}
                >
                  {text.markResolved}
                </button>
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
  const titleTooLong = length(title) > JOB_TITLE_MAX;
  const saveTitle = async () => {
    setTitleError(null);
    try {
      await updateTitle.mutateAsync({
        agentId: row.agentKey,
        jobTitle: title.trim() === "" ? null : title.trim(),
      });
    } catch (error) {
      setTitleError(errorText(error, copy));
    }
  };
  const transferOwner = async () => {
    setOwnerError(null);
    try {
      await transfer.mutateAsync({
        agentId: row.agentKey,
        newOwnerActorId: owner.trim(),
      });
      setOwner("");
    } catch (error) {
      setOwnerError(errorText(error, copy, labels.transferFailed));
    }
  };
  return (
    <>
      <section>
        <label>
          {labels.jobTitleLabel}
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        {titleTooLong ? <p role="alert">{labels.jobTitleTooLong}</p> : null}
        {titleError ? <p role="alert">{titleError}</p> : null}
        <button
          type="button"
          disabled={titleTooLong || updateTitle.isPending}
          onClick={() => void saveTitle()}
        >
          {labels.save}
        </button>
      </section>
      <section>
        <label>
          {labels.newOwnerLabel}
          <input
            value={owner}
            onChange={(event) => setOwner(event.target.value)}
          />
        </label>
        {ownerError ? <p role="alert">{ownerError}</p> : null}
        <button
          type="button"
          disabled={owner.trim() === "" || transfer.isPending}
          onClick={() => void transferOwner()}
        >
          {text.transferOwner}
        </button>
      </section>
    </>
  );
}
