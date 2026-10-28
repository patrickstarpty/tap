import {
  usePerson,
  type AgentTeamPersonView,
  type TeamPeriod,
} from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import type { ProfileTab } from "./AgentProfileDrawer";
import { Avatar } from "./avatar";
import { EMPTY, formatDateTime, teamLocale } from "./format";
import {
  LoadError,
  TeamSkeleton,
  defaultHeader,
  type RenderHeader,
} from "./layout";
import type { CellContext } from "./memberCells";
import { RosterCards } from "./RosterCards";
import { RuntimeBadge, RuntimeMark } from "./RuntimeBadge";
import { useSparklines } from "./useSparklines";

export interface MyAgentsTabProps {
  projectId: string;
  copy: WorkspaceCopy;
  period: TeamPeriod;
  onOpenMember: (agentKey: string, tab?: ProfileTab) => void;
  renderHeader?: RenderHeader;
}

type PendingItem = AgentTeamPersonView["data"]["pendingFeedback"][number];

export function MyAgentsTab({
  projectId,
  copy,
  period,
  onOpenMember,
  renderHeader = defaultHeader,
}: MyAgentsTabProps) {
  const text = copy.agentTeam;
  const labels = text.mine;
  const person = usePerson(projectId, "me", period);
  const { byAgentKey: sparklines } = useSparklines(projectId);

  if (person.isPending)
    return (
      <div className="tap-team-tab">
        {renderHeader(null)}
        <TeamSkeleton copy={copy} kpis={false} />
      </div>
    );
  if (person.isError || person.data === undefined)
    return (
      <div className="tap-team-tab">
        {renderHeader(null)}
        <LoadError copy={copy} onRetry={() => void person.refetch()} />
      </div>
    );

  const view = person.data;
  const locale = teamLocale(copy);
  const ctx: CellContext = {
    copy,
    locale,
    timezone: view.timezone,
    runtimeStatus: view.runtimeStatus,
  };
  const { owned, served, pendingFeedback } = view.data;
  const names = new Map(
    [...owned, ...served].map((row) => [row.agentKey, row.name]),
  );
  const rating = (item: PendingItem) =>
    item.rating === "up"
      ? labels.ratingUp
      : item.rating === "down"
        ? labels.ratingDown
        : labels.ratingNone;

  return (
    <div className="tap-team-tab">
      {renderHeader(
        <RuntimeBadge
          status={view.runtimeStatus}
          computedAt={view.computedAt}
          timezone={view.timezone}
          copy={copy}
        />,
      )}
      <section
        className="tap-team-section tap-team-section--first"
        aria-labelledby="my-agents-feedback"
      >
        <div className="tap-team-section-head">
          <h3 id="my-agents-feedback">{text.pendingFeedbackMine}</h3>
          {pendingFeedback.length === 0 ? null : (
            <span className="tap-team-section-count" data-tone="accent">
              {pendingFeedback.length}
            </span>
          )}
        </div>
        {pendingFeedback.length === 0 ? (
          <p className="tap-team-empty tap-team-empty--quiet">
            {labels.noFeedback}
          </p>
        ) : (
          <ul className="tap-team-feedback-list">
            {pendingFeedback.map((item) => {
              const name = names.get(item.agentKey) ?? item.agentKey;
              return (
                <li key={item.feedbackId}>
                  <button
                    type="button"
                    className="tap-team-feedback-card"
                    onClick={() => onOpenMember(item.agentKey, "feedback")}
                  >
                    <Avatar name={name} size="sm" />
                    <span className="tap-team-feedback-main">
                      <span className="tap-team-feedback-head">
                        <strong>{name}</strong>
                        <span
                          className="tap-team-rating"
                          data-rating={item.rating ?? "none"}
                        >
                          {rating(item)}
                        </span>
                        <time dateTime={item.updatedAt}>
                          {formatDateTime(
                            item.updatedAt,
                            view.timezone,
                            locale,
                          )}
                        </time>
                      </span>
                      {item.comment ? (
                        <span className="tap-team-feedback-comment">
                          {item.comment}
                        </span>
                      ) : null}
                    </span>
                    <span className="tap-team-feedback-go" aria-hidden="true">
                      ›
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
      <section className="tap-team-section" aria-labelledby="my-agents-owned">
        <div className="tap-team-section-head">
          <h3 id="my-agents-owned">{labels.ownedHeading}</h3>
          {owned.length === 0 ? null : (
            <span className="tap-team-section-count">{owned.length}</span>
          )}
        </div>
        {owned.length === 0 ? (
          <p className="tap-team-empty">{labels.noOwned}</p>
        ) : (
          <RosterCards
            rows={owned}
            sparklineByAgentKey={sparklines}
            ctx={ctx}
            onOpenMember={onOpenMember}
          />
        )}
      </section>
      <section className="tap-team-section" aria-labelledby="my-agents-served">
        <div className="tap-team-section-head">
          <h3 id="my-agents-served">{labels.servedHeading}</h3>
          {served.length === 0 ? null : (
            <span className="tap-team-section-count">{served.length}</span>
          )}
        </div>
        {served.length === 0 ? (
          <p className="tap-team-empty">
            {view.runtimeStatus === "unavailable" ? (
              // Who was served comes from run data: unknown, not nobody.
              <>
                {EMPTY}
                <RuntimeMark status="unavailable" copy={copy} />
              </>
            ) : (
              labels.noServed
            )}
          </p>
        ) : (
          <RosterCards
            rows={served}
            sparklineByAgentKey={sparklines}
            ctx={ctx}
            onOpenMember={onOpenMember}
          />
        )}
      </section>
    </div>
  );
}
