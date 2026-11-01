import { Button } from "antd";
import { useEffect, useState } from "react";

import {
  skillHubExportUrl,
  useSkillHubCatalog,
  useSkillHubSummary,
  type CatalogFilters,
  type SkillCategory,
  type SkillSourceKind,
  type SkillTask,
} from "../../../../features/knowledge/api/skillHub";
import type { WorkspaceCopy } from "../copy";
import {
  PeriodPicker,
  useValidPeriod,
  type PeriodSelection,
} from "../agents/team/PeriodPicker";
import { RuntimeBadge } from "../agents/team/RuntimeBadge";
import { formatCount, formatPercent, teamLocale } from "../agents/team/format";
import { SkillCard } from "./SkillCard";
import "../agents/team/team.css";
import "./skillHub.css";

export interface SkillLibraryProps {
  projectId: string;
  copy: WorkspaceCopy;
  selection: PeriodSelection;
  onSelectionChange: (selection: PeriodSelection) => void;
  onOpen: (latestRevisionId: string) => void;
  onImport: () => void;
  onCreate: () => void;
}

const CATEGORIES: SkillCategory[] = [
  "test-design",
  "automation",
  "knowledge",
  "analysis",
  "writing",
  "other",
];
const TASKS: SkillTask[] = [
  "knowledge.answer",
  "test-plan.generate",
  "automation.generate",
];
const SOURCES: SkillSourceKind[] = ["imported", "authored", "built_in"];
const STATUSES = ["enabled", "disabled"] as const;

type SortField = NonNullable<CatalogFilters["sort"]>;
/** Each sort reads naturally in one direction: most used, A–Z, newest. */
const SORT_ORDER: Record<SortField, "asc" | "desc"> = {
  activations: "desc",
  name: "asc",
  last_used_at: "desc",
  updated_at: "desc",
};
const SORTS = Object.keys(SORT_ORDER) as SortField[];

const SEARCH_DELAY_MS = 250;

/** Waits for typing to pause before a search fires a request. */
function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

interface FilterSelectProps<T extends string> {
  label: string;
  allLabel?: string;
  value: T | "";
  options: readonly T[];
  optionLabel: (value: T) => string;
  onChange: (value: T | "") => void;
}

function FilterSelect<T extends string>({
  label,
  allLabel,
  value,
  options,
  optionLabel,
  onChange,
}: FilterSelectProps<T>) {
  return (
    <label>
      <span>{label}</span>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value as T | "")}
      >
        {allLabel === undefined ? null : <option value="">{allLabel}</option>}
        {options.map((option) => (
          <option key={option} value={option}>
            {optionLabel(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

export function SkillLibrary({
  projectId,
  copy,
  selection,
  onSelectionChange,
  onOpen,
  onImport,
  onCreate,
}: SkillLibraryProps) {
  const text = copy.skillHub;
  const locale = teamLocale(copy);
  // An inverted or half-typed custom range never queries; the last valid
  // period's library stays on screen under the picker's inline message.
  const { period } = useValidPeriod(selection);

  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), SEARCH_DELAY_MS);
  const [category, setCategory] = useState<SkillCategory | "">("");
  const [task, setTask] = useState<SkillTask | "">("");
  const [source, setSource] = useState<SkillSourceKind | "">("");
  const [status, setStatus] = useState<(typeof STATUSES)[number] | "">("");
  const [sort, setSort] = useState<SortField>("activations");
  const filtered =
    q !== "" ||
    category !== "" ||
    task !== "" ||
    source !== "" ||
    status !== "";

  const catalog = useSkillHubCatalog(projectId, period, {
    q,
    category: category === "" ? undefined : category,
    task: task === "" ? undefined : task,
    source: source === "" ? undefined : source,
    status: status === "" ? undefined : status,
    sort,
    order: SORT_ORDER[sort],
  });
  const summaryQuery = useSkillHubSummary(projectId, period);
  const view = catalog.data;
  const summary = summaryQuery.data?.data;
  const items = view?.data ?? [];

  const summaryTiles: { key: string; label: string; value: string }[] = [
    {
      key: "skillTotal",
      label: text.summary.skillTotal,
      value: formatCount(summary?.skillTotal, locale),
    },
    {
      key: "enabled",
      label: text.summary.enabled,
      value: formatCount(summary?.enabled, locale),
    },
    {
      key: "usedInPeriod",
      label: text.summary.usedInPeriod,
      value: formatCount(summary?.usedInPeriod, locale),
    },
    {
      key: "unused",
      label: text.summary.unused,
      value: formatCount(summary?.unused, locale),
    },
    {
      key: "autoShare",
      label: text.summary.autoShare,
      value: formatPercent(summary?.autoShare, locale),
    },
  ];

  return (
    <div className="tap-skill-hub">
      {summary !== undefined &&
      summary.candidateCount > summary.candidateLimit ? (
        <p role="status" className="tap-skill-hub-notice">
          {text.candidateNotice.replace(
            "{limit}",
            formatCount(summary.candidateLimit, locale),
          )}
        </p>
      ) : null}

      <div className="tap-skill-hub-toolbar">
        <PeriodPicker
          copy={copy}
          value={selection}
          onChange={onSelectionChange}
        />
        <div className="tap-skill-hub-toolbar-end">
          {view === undefined ? null : (
            <RuntimeBadge
              status={view.runtimeStatus}
              computedAt={view.computedAt}
              timezone={view.timezone}
              copy={copy}
            />
          )}
          <Button href={skillHubExportUrl(projectId, period)} download>
            {text.exportCsv}
          </Button>
        </div>
      </div>

      <section className="tap-skill-hub-summary" aria-label={text.summaryLabel}>
        <dl>
          {summaryTiles.map((tile) => (
            <div key={tile.key}>
              <dt>{tile.label}</dt>
              <dd>{tile.value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <div className="tap-skill-hub-filters">
        <label className="tap-skill-hub-search">
          <span>{text.searchLabel}</span>
          <input
            type="search"
            aria-label={text.searchLabel}
            placeholder={text.searchPlaceholder}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <FilterSelect
          label={text.categoryFilter}
          allLabel={text.allCategories}
          value={category}
          options={CATEGORIES}
          optionLabel={(value) => text.categories[value]}
          onChange={setCategory}
        />
        <FilterSelect
          label={text.taskFilter}
          allLabel={text.allTasks}
          value={task}
          options={TASKS}
          optionLabel={(value) => copy.skillsWorkspace.taskLabels[value]}
          onChange={setTask}
        />
        <FilterSelect
          label={text.sourceFilter}
          allLabel={text.allSources}
          value={source}
          options={SOURCES}
          optionLabel={(value) => text.sources[value]}
          onChange={setSource}
        />
        <FilterSelect
          label={text.statusFilter}
          allLabel={text.allStatuses}
          value={status}
          options={STATUSES}
          optionLabel={(value) => text.statuses[value]}
          onChange={setStatus}
        />
        <FilterSelect
          label={text.sortLabel}
          value={sort}
          options={SORTS}
          optionLabel={(value) => text.sortOptions[value]}
          onChange={(value) => {
            if (value !== "") setSort(value);
          }}
        />
      </div>

      {view === undefined ? (
        catalog.isError ? (
          <div role="alert" className="tap-skill-hub-state">
            <p>{text.loadFailed}</p>
            <Button onClick={() => void catalog.refetch()}>{text.retry}</Button>
          </div>
        ) : (
          <p role="status" className="tap-skill-hub-state">
            {text.loading}
          </p>
        )
      ) : items.length === 0 ? (
        filtered ? (
          <p className="tap-skill-hub-state">{text.noResults}</p>
        ) : (
          <div className="tap-skill-hub-empty">
            <h2>{text.emptyTitle}</h2>
            <p>{text.emptyDescription}</p>
            <div className="tap-skill-hub-empty-actions">
              <Button type="primary" onClick={onImport}>
                {text.importSkill}
              </Button>
              <Button onClick={onCreate}>{text.createSkill}</Button>
            </div>
          </div>
        )
      ) : (
        <ul className="tap-skill-hub-grid" aria-label={text.libraryLabel}>
          {items.map((item) => (
            <SkillCard
              key={item.skill.skillId}
              item={item}
              copy={copy}
              onOpen={onOpen}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
