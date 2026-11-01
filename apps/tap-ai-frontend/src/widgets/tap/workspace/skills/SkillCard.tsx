import type { SkillHubCatalogItem } from "../../../../features/knowledge/api/skillHub";
import type { WorkspaceCopy } from "../copy";
import { formatCount, teamLocale } from "../agents/team/format";

export interface SkillCardProps {
  item: SkillHubCatalogItem;
  copy: WorkspaceCopy;
  onOpen: (latestRevisionId: string) => void;
}

function formatUpdated(value: string, locale: string): string {
  const time = Date.parse(value);
  if (Number.isNaN(time)) return value;
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(time);
}

/** One Skill in the library: what it is, where it came from and how often
 * it was used in the selected period. */
export function SkillCard({ item, copy, onOpen }: SkillCardProps) {
  const text = copy.skillHub;
  const fields = text.cardFields;
  const locale = teamLocale(copy);
  const { skill } = item;
  return (
    <li className="tap-skill-card" aria-label={skill.name}>
      <article>
        <header className="tap-skill-card-title">
          <h2>
            <button
              type="button"
              onClick={() => onOpen(skill.latestRevisionId)}
            >
              {skill.name}
            </button>
          </h2>
          <span className="tap-skill-card-status" data-status={skill.status}>
            {text.statuses[skill.status]}
          </span>
        </header>
        {skill.description ? (
          <p className="tap-skill-card-description">{skill.description}</p>
        ) : null}
        <dl className="tap-skill-card-meta">
          <div>
            <dt>{fields.category}</dt>
            <dd>{text.categories[skill.category]}</dd>
          </div>
          <div>
            <dt>{fields.version}</dt>
            <dd>{skill.version}</dd>
          </div>
          <div>
            <dt>{fields.source}</dt>
            <dd data-source-kind={skill.sourceKind}>
              {text.sources[skill.sourceKind]}
            </dd>
          </div>
          <div>
            <dt>{fields.updated}</dt>
            <dd>
              <time dateTime={skill.updatedAt}>
                {formatUpdated(skill.updatedAt, locale)}
              </time>
            </dd>
          </div>
          <div title={text.activationsHint}>
            <dt>{fields.activations}</dt>
            <dd>{formatCount(item.activations, locale)}</dd>
          </div>
        </dl>
      </article>
    </li>
  );
}
