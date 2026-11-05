import { Button } from "antd";
import { useEffect, useRef, useState, type MouseEvent } from "react";

import { resolvePeriod } from "../../../../features/knowledge/api/agentTeam";
import type { SkillRevisionDetail } from "../../../../features/knowledge/api/aiAssets";
import {
  useValidPeriod,
  type PeriodSelection,
} from "../agents/team/PeriodPicker";
import type { WorkspaceCopy } from "../copy";
import { SkillDetailDrawer } from "./SkillDetailDrawer";
import { SkillForm } from "./SkillForm";
import { SkillImportDialog } from "./SkillImportDialog";
import { SkillLibrary } from "./SkillLibrary";

export interface SkillsWorkspaceProps {
  projectId: string;
  copy: WorkspaceCopy;
  onUse: (revisionId: string) => void;
  /** Opens this revision's detail drawer once, e.g. when a turn's "Sources
   * and settings" link names a Skill revision. */
  initialDetailRevisionId?: string | null;
  /** Called once `initialDetailRevisionId` has been applied, so the
   * caller can clear it — otherwise a later remount (this workspace is
   * conditionally rendered, so switching modules away and back unmounts
   * it) would see the same non-null id again and reopen the same
   * revision's drawer on every later visit. */
  onInitialDetailConsumed?: () => void;
}

type FormState =
  | { mode: "create" }
  | { mode: "edit"; skill: SkillRevisionDetail }
  | { mode: "copy"; skill: SkillRevisionDetail };

export function SkillsWorkspace({
  projectId,
  copy,
  onUse,
  initialDetailRevisionId = null,
  onInitialDetailConsumed,
}: SkillsWorkspaceProps) {
  const text = copy.skillsWorkspace;
  const [selection, setSelection] = useState<PeriodSelection>(() => ({
    preset: "last30",
    custom: resolvePeriod("last30", new Date()),
  }));
  // An inverted or half-typed custom range never queries; the library and
  // the detail drawer keep the last valid period meanwhile.
  const { period } = useValidPeriod(selection);
  const [importOpen, setImportOpen] = useState(false);
  const [detailRevisionId, setDetailRevisionId] = useState<string | null>(null);
  const [formState, setFormState] = useState<FormState | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const consumedInitialDetailRef = useRef<string | null>(null);

  useEffect(() => {
    if (
      initialDetailRevisionId !== null &&
      consumedInitialDetailRef.current !== initialDetailRevisionId
    ) {
      consumedInitialDetailRef.current = initialDetailRevisionId;
      setDetailRevisionId(initialDetailRevisionId);
      onInitialDetailConsumed?.();
    }
  }, [initialDetailRevisionId, onInitialDetailConsumed]);

  // Library controls report no event; the focused control opened the
  // dialog, so closing it hands focus back there.
  const rememberFocus = () => {
    triggerRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
  };

  const openImport = (event?: MouseEvent<HTMLElement>) => {
    if (event === undefined) rememberFocus();
    else triggerRef.current = event.currentTarget;
    setImportOpen(true);
  };

  const openCreate = (event?: MouseEvent<HTMLElement>) => {
    if (event === undefined) rememberFocus();
    else triggerRef.current = event.currentTarget;
    setFormState({ mode: "create" });
  };

  const openDetail = (revisionId: string) => {
    rememberFocus();
    setDetailRevisionId(revisionId);
  };

  return (
    <section
      className="tap-module tap-skills-workspace"
      aria-labelledby="skills-heading"
    >
      <header className="tap-module-heading">
        <div>
          <h1 id="skills-heading">{text.heading}</h1>
          <p>{text.description}</p>
        </div>
        <div className="tap-skills-header-actions">
          <Button onClick={openImport}>{text.importSkill}</Button>
          <Button type="primary" onClick={openCreate}>
            {text.createSkill}
          </Button>
        </div>
      </header>

      <SkillLibrary
        projectId={projectId}
        copy={copy}
        selection={selection}
        period={period}
        onSelectionChange={setSelection}
        onOpen={openDetail}
        onImport={() => openImport()}
        onCreate={() => openCreate()}
      />

      {importOpen ? (
        <SkillImportDialog
          projectId={projectId}
          copy={copy}
          onClose={() => setImportOpen(false)}
          opener={triggerRef.current}
        />
      ) : null}

      {detailRevisionId !== null ? (
        <SkillDetailDrawer
          projectId={projectId}
          copy={copy}
          revisionId={detailRevisionId}
          period={period}
          onClose={() => setDetailRevisionId(null)}
          onUse={onUse}
          onEdit={(detail) => {
            setDetailRevisionId(null);
            setFormState({ mode: "edit", skill: detail });
          }}
          onCopyAsCustom={(detail) => {
            setDetailRevisionId(null);
            setFormState({ mode: "copy", skill: detail });
          }}
          opener={triggerRef.current}
        />
      ) : null}

      {formState !== null ? (
        <SkillForm
          projectId={projectId}
          copy={copy}
          mode={formState.mode}
          skill={formState.mode === "create" ? undefined : formState.skill}
          onClose={() => setFormState(null)}
          opener={triggerRef.current}
        />
      ) : null}
    </section>
  );
}
