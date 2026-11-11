import { useState } from "react";
import type {
  BddStep,
  LcaClient,
  LcaDefinition,
  LcaStep,
  Operation,
} from "./client";
import { FileAssetArguments } from "./FileAssetArguments";
import { PageContext } from "./PageContext";
import { pageLifecycle } from "./pageBindings";
import { ActionArguments } from "./ActionArguments";
import { DefinitionSettings } from "./DefinitionSettings";
import {
  actionSummary,
  argumentFields,
  groupsFor,
  identifier,
  moveAction,
  needsTarget,
  newAction,
  operationLabels,
  withGroups,
} from "./bdd";
export { parseParameterValue } from "./DefinitionSettings";

export function DefinitionEditor({
  value,
  onChange: emitChange,
  disabled = false,
  selectedGroupId,
  onSelectGroup,
  projectId,
  client,
}: {
  value: LcaDefinition;
  onChange(value: LcaDefinition): void;
  disabled?: boolean;
  projectId?: string;
  client?: LcaClient;
  selectedGroupId?: string;
  onSelectGroup?(id: string): void;
}) {
  const groups = groupsFor(value);
  const onChange = (next: LcaDefinition) =>
    emitChange(withGroups(next, groupsFor(next)));
  const [localGroup, setLocalGroup] = useState(groups[0].id);
  const [actionId, setActionId] = useState<string | null>(
    value.steps[0]?.id ?? null,
  );
  const group =
    groups.find((g) => g.id === (selectedGroupId ?? localGroup)) ?? groups[0];
  const actions = value.steps.filter((s) => group.action_ids.includes(s.id));
  const selected = actions.find((s) => s.id === actionId) ?? actions[0];
  const selectGroup = (id: string) => {
    setLocalGroup(id);
    onSelectGroup?.(id);
    setActionId(null);
  };
  const updateGroups = (next: BddStep[], definition = value) =>
    onChange(withGroups(definition, next));
  const changeGroup = (patch: Partial<BddStep>) =>
    updateGroups(
      groups.map((g) => (g.id === group.id ? { ...g, ...patch } : g)),
    );
  const changeAction = (next: LcaStep) =>
    onChange(
      withGroups(
        {
          ...value,
          steps: value.steps.map((s) => (s.id === next.id ? next : s)),
        },
        groups,
      ),
    );
  const add = (operation: Operation) => {
    const action = newAction(operation, value);
    updateGroups(
      groups.map((g) =>
        g.id === group.id
          ? { ...g, action_ids: [...g.action_ids, action.id] }
          : g,
      ),
      { ...value, steps: [...value.steps, action] },
    );
    setActionId(action.id);
  };
  const moveGroup = (delta: number) => {
    const next = [...groups],
      i = groups.indexOf(group);
    [next[i], next[i + delta]] = [next[i + delta], next[i]];
    updateGroups(next);
  };
  const move = (id: string, delta: number) => {
    const ids = [...group.action_ids],
      i = ids.indexOf(id);
    [ids[i], ids[i + delta]] = [ids[i + delta], ids[i]];
    changeGroup({ action_ids: ids });
  };
  return (
    <fieldset className="lca-definition" disabled={disabled}>
      <legend>Business steps & actions</legend>
      <div className="lca-editor-caption">
        <p>
          Describe the business intent, then arrange the actions that implement
          it.
        </p>
        <span>
          {groups.length} BDD steps · {value.steps.length}/100 actions
        </span>
      </div>
      <div className="lca-bdd-editor">
        <nav className="lca-bdd-tree" aria-label="BDD steps">
          <div className="lca-pane-heading">
            <strong>Business steps</strong>
            <span>BDD</span>
          </div>
          <ol>
            {groups.map((g, index) => (
              <li key={g.id}>
                <button
                  type="button"
                  aria-pressed={g.id === group.id}
                  onClick={() => selectGroup(g.id)}
                >
                  <span className="lca-step-number">{index + 1}</span>
                  <span>
                    <b>{g.keyword}</b> {g.description}
                    <small>{g.action_ids.length} actions</small>
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <button
            type="button"
            disabled={groups.length >= 100}
            onClick={() => {
              const next: BddStep = {
                id: identifier("bdd"),
                keyword: "When",
                description: "Describe the business step",
                action_ids: [],
              };
              updateGroups([...groups, next]);
              selectGroup(next.id);
            }}
          >
            Add BDD step
          </button>
        </nav>
        <section className="lca-action-canvas" aria-label="BDD action sequence">
          <div className="lca-pane-heading">
            <strong>Action sequence</strong>
            <span>
              {groups.indexOf(group) + 1} of {groups.length}
            </span>
          </div>
          <div className="lca-bdd-title">
            <label>
              BDD keyword
              <select
                aria-label="BDD keyword"
                value={group.keyword}
                onChange={(e) =>
                  changeGroup({ keyword: e.target.value as BddStep["keyword"] })
                }
              >
                {["Given", "When", "Then", "And", "But"].map((k) => (
                  <option key={k}>{k}</option>
                ))}
              </select>
            </label>
            <label>
              BDD description
              <input
                maxLength={1024}
                value={group.description}
                onChange={(e) => changeGroup({ description: e.target.value })}
              />
            </label>
          </div>
          <div className="lca-sequence-tools">
            <button
              type="button"
              aria-label="Move BDD step up"
              disabled={groups.indexOf(group) === 0}
              onClick={() => moveGroup(-1)}
            >
              ↑
            </button>
            <button
              type="button"
              aria-label="Move BDD step down"
              disabled={groups.indexOf(group) === groups.length - 1}
              onClick={() => moveGroup(1)}
            >
              ↓
            </button>
            <button
              type="button"
              disabled={groups.length === 1 || group.action_ids.length > 0}
              title={
                group.action_ids.length
                  ? "Move or remove this step's actions first"
                  : "Remove empty step"
              }
              onClick={() => {
                updateGroups(groups.filter((g) => g.id !== group.id));
                selectGroup(groups.find((g) => g.id !== group.id)!.id);
              }}
            >
              Remove empty step
            </button>
          </div>
          {!actions.length && (
            <div className="lca-sequence-empty">
              <strong>Add the first action</strong>
              <p>
                Use actions below, or import a recording into this business
                step.
              </p>
            </div>
          )}
          <ol className="lca-action-list">
            {actions.map((action, index) => (
              <li key={action.id}>
                <button
                  type="button"
                  className="lca-action-card"
                  aria-pressed={selected?.id === action.id}
                  onClick={() => setActionId(action.id)}
                  aria-label={`Edit action ${index + 1}: ${operationLabels[action.operation]}`}
                >
                  <span className="lca-step-number">{index + 1}</span>
                  <span>
                    <strong>{operationLabels[action.operation]}</strong>
                    <small>{actionSummary(action, value)}</small>
                  </span>
                  <span aria-hidden="true">›</span>
                </button>
                <div className="lca-action-reorder">
                  <button
                    type="button"
                    aria-label={`Move ${action.id} up`}
                    disabled={index === 0}
                    onClick={() => move(action.id, -1)}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    aria-label={`Move ${action.id} down`}
                    disabled={index === actions.length - 1}
                    onClick={() => move(action.id, 1)}
                  >
                    ↓
                  </button>
                </div>
              </li>
            ))}
          </ol>
          <div className="lca-actions">
            <button
              type="button"
              disabled={value.steps.length >= 100}
              onClick={() => add("click")}
            >
              Add action
            </button>
            <button
              type="button"
              disabled={value.steps.length >= 100}
              onClick={() => add("assert_url")}
            >
              Add assertion
            </button>
          </div>
          <p className="lca-muted">
            Actions execute in this order. Assertions verify the business
            outcome.
          </p>
        </section>
        <aside className="lca-action-properties" aria-label="Action properties">
          <div className="lca-pane-heading">
            <strong>Properties</strong>
            <span>
              {selected ? operationLabels[selected.operation] : "No action"}
            </span>
          </div>
          {selected ? (
            <ActionProperties
              key={selected.id}
              step={selected}
              value={value}
              projectId={projectId}
              client={client}
              groups={groups}
              groupId={group.id}
              onChange={changeAction}
              onDefinitionChange={onChange}
              onMove={(id) => {
                onChange(moveAction(value, selected.id, id));
                selectGroup(id);
                setActionId(selected.id);
              }}
              onRemove={() => {
                updateGroups(
                  groups.map((g) => ({
                    ...g,
                    action_ids: g.action_ids.filter((id) => id !== selected.id),
                  })),
                  {
                    ...value,
                    steps: value.steps.filter((s) => s.id !== selected.id),
                  },
                );
                setActionId(null);
              }}
            />
          ) : (
            <p className="lca-muted">
              Select or add an action to configure its target and value.
            </p>
          )}
        </aside>
      </div>
      <details className="lca-flow-settings">
        <summary>Flow settings · origins, targets & parameters</summary>
        <DefinitionSettings value={value} onChange={onChange} />
      </details>
      <details>
        <summary>Canonical definition (read-only)</summary>
        <pre>{JSON.stringify(value, null, 2)}</pre>
      </details>
    </fieldset>
  );
}
function ActionProperties({
  step,
  value,
  groups,
  groupId,
  onChange,
  onDefinitionChange,
  onMove,
  onRemove,
  projectId,
  client,
}: {
  step: LcaStep;
  value: LcaDefinition;
  projectId?: string;
  client?: LcaClient;
  groups: BddStep[];
  groupId: string;
  onChange(step: LcaStep): void;
  onDefinitionChange(value: LcaDefinition): void;
  onMove(id: string): void;
  onRemove(): void;
}) {
  const target = value.targets.find((t) => t.id === step.target_id);
  const changeTarget = (patch: Partial<NonNullable<typeof target>>) =>
    onDefinitionChange({
      ...value,
      targets: value.targets.map((t) =>
        t.id === target?.id ? { ...t, ...patch } : t,
      ),
    });
  return (
    <>
      <label>
        Operation
        <select
          aria-label={`Step ${step.id} operation`}
          value={step.operation}
          onChange={(e) => {
            const operation = e.target.value as Operation;
            const defaults = newAction(operation, value);
            const next = {
              ...step,
              operation,
              arguments: defaults.arguments,
              target_id: needsTarget(operation)
                ? (value.targets[0]?.id ?? null)
                : null,
            };
            if (pageLifecycle(next)) delete next.page_id;
            onChange(next);
          }}
        >
          {(Object.keys(operationLabels) as Operation[]).map((op) => (
            <option key={op} value={op}>
              {operationLabels[op]}
            </option>
          ))}
        </select>
      </label>
      <PageContext step={step} definition={value} onChange={onChange} />
      {(needsTarget(step.operation) || step.operation === "scroll") && (
        <>
          <label>
            Target
            <select
              aria-label={`Step ${step.id} target`}
              value={step.target_id || ""}
              onChange={(e) =>
                onChange({ ...step, target_id: e.target.value || null })
              }
            >
              <option value="">
                {step.operation === "scroll"
                  ? "Page (no target)"
                  : "Choose a target"}
              </option>
              {value.targets.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name || t.value || t.id}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={value.targets.length >= 100}
            onClick={() => {
              const id = identifier("target");
              onDefinitionChange(
                withGroups(
                  {
                    ...value,
                    targets: [
                      ...value.targets,
                      { id, kind: "test_id", value: "" },
                    ],
                    steps: value.steps.map((s) =>
                      s.id === step.id ? { ...s, target_id: id } : s,
                    ),
                  },
                  groups,
                ),
              );
            }}
          >
            New target
          </button>
        </>
      )}
      {target && (
        <div className="lca-target-properties">
          <label>
            Locator strategy
            <select
              aria-label="Locator strategy"
              value={target.kind}
              onChange={(e) => {
                const kind = e.target.value as typeof target.kind;
                onDefinitionChange({
                  ...value,
                  targets: value.targets.map((t) =>
                    t.id === target.id
                      ? {
                          id: t.id,
                          kind,
                          value: "",
                          ...(kind === "role" ? { name: "" } : {}),
                          review_required: kind === "css",
                          frame_target_id: t.frame_target_id,
                        }
                      : t,
                  ),
                });
              }}
            >
              <option value="test_id">Test ID</option>
              <option value="role">Role and name</option>
              <option value="css">CSS (review required)</option>
            </select>
          </label>
          <label>
            Locator value
            <input
              value={target.value}
              onChange={(e) => changeTarget({ value: e.target.value })}
            />
          </label>
          {target.kind === "role" && (
            <label>
              Accessible name
              <input
                value={target.name || ""}
                onChange={(e) => changeTarget({ name: e.target.value })}
              />
            </label>
          )}
          <label>
            Frame context
            <select
              aria-label="Frame context"
              value={target.frame_target_id || ""}
              onChange={(e) =>
                changeTarget({ frame_target_id: e.target.value || null })
              }
            >
              <option value="">Top-level page</option>
              {value.targets
                .filter(
                  (t) =>
                    t.id !== target.id &&
                    !t.frame_target_id &&
                    !value.targets.some(
                      (child) => child.frame_target_id === target.id,
                    ),
                )
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name || t.value || t.id}
                  </option>
                ))}
            </select>
          </label>
          <p className="lca-muted">
            For iframe content, select a top-level target that locates the
            iframe. Nested frames are not supported.
          </p>
          <p className="lca-muted">
            Shared by{" "}
            {value.steps.filter((s) => s.target_id === target.id).length}{" "}
            action(s). Editing changes every reference.
            {target.kind === "css"
              ? " Review this CSS locator before replay."
              : ""}
          </p>
        </div>
      )}
      {step.operation === "upload_file" ? (
        <FileAssetArguments
          key={step.id}
          step={step}
          projectId={projectId}
          client={client}
          onChange={onChange}
        />
      ) : (
        <ActionArguments
          key={step.operation}
          step={step}
          definition={value}
          fields={argumentFields(step.operation)}
          onChange={onChange}
        />
      )}
      {step.operation === "download_file" && (
        <p className="lca-muted">
          Click the target and capture one download, up to 5 MB. Retrieve the
          original bytes from this run’s evidence after replay. No destination
          path is accepted.
        </p>
      )}
      <label>
        Timeout (ms)
        <input
          aria-label={`Step ${step.id} timeout`}
          type="number"
          min={1}
          max={30000}
          value={step.timeout_ms}
          onChange={(e) =>
            onChange({ ...step, timeout_ms: Number(e.target.value) })
          }
        />
      </label>
      <label>
        Move to BDD step
        <select value={groupId} onChange={(e) => onMove(e.target.value)}>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.keyword} {g.description}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        disabled={value.steps.length === 1}
        onClick={onRemove}
      >
        Remove {step.id}
      </button>
      <small className="lca-action-id">Action ID: {step.id}</small>
    </>
  );
}
