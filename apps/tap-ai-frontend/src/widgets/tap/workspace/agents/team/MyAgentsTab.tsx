import {
  usePerson,
  type AgentTeamPersonView,
  type TeamPeriod,
} from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { formatDateTime, teamLocale } from "./format";
import type { CellContext } from "./memberCells";
import { RosterCards } from "./RosterCards";
import { RuntimeBadge } from "./RuntimeBadge";
import { useSparklines } from "./useSparklines";

export interface MyAgentsTabProps {
  projectId: string;
  copy: WorkspaceCopy;
  period: TeamPeriod;
  onOpenMember: (agentKey: string) => void;
}

type PendingItem = AgentTeamPersonView["data"]["pendingFeedback"][number];

export function MyAgentsTab({
  projectId,
  copy,
  period,
  onOpenMember,
}: MyAgentsTabProps) {
  const text = copy.agentTeam;
  const labels = text.mine;
  const person = usePerson(projectId, "me", period);
  const sparklines = useSparklines(projectId);

  if (person.isPending) return <p role="status">{text.loading}</p>;
  if (person.isError || person.data === undefined)
    return (
      <div role="alert">
        <p>{text.loadFailed}</p>
        <button type="button" onClick={() => void person.refetch()}>
          {text.retry}
        </button>
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
      <RuntimeBadge
        status={view.runtimeStatus}
        computedAt={view.computedAt}
        timezone={view.timezone}
        copy={copy}
      />
      <section aria-labelledby="my-agents-feedback">
        <h3 id="my-agents-feedback">{text.pendingFeedbackMine}</h3>
        {pendingFeedback.length === 0 ? (
          <p className="tap-team-empty">{labels.noFeedback}</p>
        ) : (
          <ul className="tap-team-feedback">
            {pendingFeedback.map((item) => (
              <li key={item.feedbackId}>
                <button
                  type="button"
                  onClick={() => onOpenMember(item.agentKey)}
                >
                  {names.get(item.agentKey) ?? item.agentKey}
                  {" · "}
                  {rating(item)}
                  {" · "}
                  {formatDateTime(item.updatedAt, view.timezone, locale)}
                </button>
                {item.comment ? <p>{item.comment}</p> : null}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="my-agents-owned">
        <h3 id="my-agents-owned">{labels.ownedHeading}</h3>
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
      <section aria-labelledby="my-agents-served">
        <h3 id="my-agents-served">{labels.servedHeading}</h3>
        {served.length === 0 ? (
          <p className="tap-team-empty">{labels.noServed}</p>
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
