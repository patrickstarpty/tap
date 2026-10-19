import { Button, Input } from "antd";
import { useRef, useState } from "react";

import {
  AiAssetError,
  useCommitSkillImport,
  useStageSkillImport,
  type SkillImportCommitResult,
  type SkillImportPreview,
} from "../../../../features/knowledge/api/aiAssets";
import { AccessibleDialog } from "../AccessibleDialog";
import type { WorkspaceCopy } from "../copy";

// I-8: map the server's closed error-code vocabulary to prose instead of a
// single generic message, so e.g. a 413 package-too-large and a 409
// name-conflict don't read identically.
function importErrorMessage(
  text: WorkspaceCopy["skillsWorkspace"]["import"],
  cause: unknown,
  fallback: string,
): string {
  if (cause instanceof AiAssetError) {
    const known = text.errors as Record<string, string | undefined>;
    const message = known[cause.code];
    if (message !== undefined) return message;
  }
  return fallback;
}

export interface SkillImportDialogProps {
  projectId: string;
  copy: WorkspaceCopy;
  onClose: () => void;
  onImported?: (result: SkillImportCommitResult) => void;
  opener: HTMLElement | null;
}

type ItemDecision = "import" | "skip";

function categoryLabel(
  copy: WorkspaceCopy,
  category: string,
  attached: boolean,
): string {
  const labels = copy.skillsWorkspace.import.category;
  if (category === "skill") return labels.skill;
  if (category === "reference")
    return attached ? labels.referenceAttached : labels.referenceUnattached;
  if (category === "script") return labels.script;
  return labels.asset;
}

export function SkillImportDialog({
  projectId,
  copy,
  onClose,
  onImported,
  opener,
}: SkillImportDialogProps) {
  const text = copy.skillsWorkspace.import;
  const [step, setStep] = useState<1 | 2>(1);
  const [pastedText, setPastedText] = useState("");
  const [archiveFile, setArchiveFile] = useState<File | null>(null);
  const [folderFiles, setFolderFiles] = useState<File[] | null>(null);
  const [preview, setPreview] = useState<SkillImportPreview | null>(null);
  const [decisions, setDecisions] = useState<Record<string, ItemDecision>>({});
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const stageImport = useStageSkillImport(projectId);
  const commitImport = useCommitSkillImport(projectId);

  const runStage = async () => {
    setError(null);
    try {
      let result: SkillImportPreview;
      if (archiveFile !== null) {
        if (archiveFile.name.toLowerCase().endsWith(".zip")) {
          result = await stageImport.mutateAsync({ archive: archiveFile });
        } else {
          // A single non-archive upload is always *the* SKILL.md, whatever
          // the browser's picked filename happens to be (e.g.
          // "policy-lookup.md") — the backend only recognizes the exact
          // basename "SKILL.md" inside a staged package and otherwise
          // 400s with skill_not_found_in_package.
          result = await stageImport.mutateAsync({
            files: [{ file: archiveFile, relativePath: "SKILL.md" }],
          });
        }
      } else if (folderFiles !== null && folderFiles.length > 0) {
        result = await stageImport.mutateAsync({
          files: folderFiles.map((file) => ({
            file,
            relativePath:
              (file as File & { webkitRelativePath?: string })
                .webkitRelativePath || file.name,
          })),
        });
      } else {
        result = await stageImport.mutateAsync({ text: pastedText });
      }
      setPreview(result);
      const initialDecisions: Record<string, ItemDecision> = {};
      for (const item of result.skills) {
        initialDecisions[item.root] =
          item.conflictSkillId != null ? "skip" : "import";
      }
      setDecisions(initialDecisions);
      setStep(2);
    } catch (cause) {
      setError(importErrorMessage(text, cause, text.stageFailed));
    }
  };

  const commit = async () => {
    if (preview === null) return;
    setError(null);
    const selections = preview.skills
      .filter((item) => decisions[item.root] !== "skip")
      .map((item) => ({
        root: item.root,
        asNewRevisionOf: item.conflictSkillId ?? null,
      }));
    try {
      const result = await commitImport.mutateAsync({
        importId: preview.importId,
        selections,
      });
      onImported?.(result);
      onClose();
    } catch (cause) {
      setError(importErrorMessage(text, cause, text.commitFailed));
      // import_expired (I-8): the staged import no longer exists server
      // side, so there is nothing left to review -- send the person back
      // to step 1 to start a fresh import rather than leaving them on a
      // step 2 preview that can never successfully commit.
      if (cause instanceof AiAssetError && cause.code === "import_expired") {
        setStep(1);
      }
    }
  };

  const canPreview =
    archiveFile !== null ||
    (folderFiles !== null && folderFiles.length > 0) ||
    pastedText.trim().length > 0;

  return (
    <AccessibleDialog
      ariaLabel={step === 1 ? text.step1Title : text.step2Title}
      className="tap-skill-import-dialog tap-catalog-dialog"
      onClose={onClose}
      opener={opener}
    >
      {step === 1 ? (
        <section>
          <header>
            <h2>{text.step1Title}</h2>
            <p>{text.step1Description}</p>
          </header>
          <div className="tap-skill-import-sources">
            <label>
              <span>{text.browseFiles}</span>
              <input
                ref={fileInputRef}
                type="file"
                aria-label={text.browseFiles}
                accept=".md,.zip"
                onChange={(event) => {
                  const file = event.target.files?.[0] ?? null;
                  setArchiveFile(file);
                  setFolderFiles(null);
                }}
              />
            </label>
            <label>
              <span>{text.browseFolder}</span>
              <input
                ref={folderInputRef}
                type="file"
                aria-label={text.browseFolder}
                multiple
                // `webkitdirectory` has no typed React prop; set via the
                // DOM attribute spread below so a folder picker (not a
                // multi-file picker) opens in supporting browsers.
                {...{ webkitdirectory: "" }}
                onChange={(event) => {
                  const files = Array.from(event.target.files ?? []);
                  setFolderFiles(files);
                  setArchiveFile(null);
                }}
              />
            </label>
            <label>
              <span>{text.pasteLabel}</span>
              <Input.TextArea
                aria-label={text.pasteLabel}
                placeholder={text.pastePlaceholder}
                rows={6}
                value={pastedText}
                onChange={(event) => {
                  setPastedText(event.target.value);
                  setArchiveFile(null);
                  setFolderFiles(null);
                }}
              />
            </label>
          </div>
          {error !== null ? <p role="alert">{error}</p> : null}
          <div className="tap-dialog-actions">
            <Button onClick={onClose}>{copy.skillsWorkspace.cancel}</Button>
            <Button
              type="primary"
              disabled={!canPreview || stageImport.isPending}
              loading={stageImport.isPending}
              onClick={() => void runStage()}
            >
              {text.next}
            </Button>
          </div>
        </section>
      ) : (
        <section>
          <header>
            <h2>{text.step2Title}</h2>
          </header>
          <ul className="tap-skill-import-list" aria-label={text.step2Title}>
            {(preview?.skills ?? []).map((item) => (
              <li key={item.root}>
                <article>
                  <h3>{item.name}</h3>
                  <p>{item.description}</p>
                  <div>
                    <strong>{text.files}</strong>
                    <ul>
                      {item.files.map((file) => (
                        <li key={file.relativePath}>
                          <span className="tap-skill-import-file-path">
                            {file.relativePath}
                          </span>
                          <span className="tap-skill-import-file-category">
                            {categoryLabel(copy, file.category, file.attached)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  {item.warnings !== undefined && item.warnings.length > 0 ? (
                    <div>
                      <strong>{text.warnings}</strong>
                      <ul>
                        {item.warnings.map((warning, index) => (
                          <li key={index}>{warning}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {item.conflictSkillId != null ? (
                    <div role="group" aria-label={text.nameConflict}>
                      <p>{text.nameConflict}</p>
                      <label>
                        <input
                          type="radio"
                          name={`conflict-${item.root}`}
                          checked={decisions[item.root] === "import"}
                          onChange={() =>
                            setDecisions((current) => ({
                              ...current,
                              [item.root]: "import",
                            }))
                          }
                        />
                        <span>{text.importAsNewRevision}</span>
                      </label>
                      <label>
                        <input
                          type="radio"
                          name={`conflict-${item.root}`}
                          checked={decisions[item.root] === "skip"}
                          onChange={() =>
                            setDecisions((current) => ({
                              ...current,
                              [item.root]: "skip",
                            }))
                          }
                        />
                        <span>{text.skip}</span>
                      </label>
                    </div>
                  ) : null}
                </article>
              </li>
            ))}
          </ul>
          {error !== null ? <p role="alert">{error}</p> : null}
          <div className="tap-dialog-actions">
            <Button onClick={() => setStep(1)}>{text.back}</Button>
            <Button
              type="primary"
              disabled={commitImport.isPending}
              loading={commitImport.isPending}
              onClick={() => void commit()}
            >
              {text.commit}
            </Button>
          </div>
        </section>
      )}
    </AccessibleDialog>
  );
}
