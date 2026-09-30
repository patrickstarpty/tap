import { useState } from "react";
import { Button } from "antd";
import { AccessibleDialog } from "../../../legacy/AccessibleDialog";
import type { PrototypeCopy } from "./copy";
import type { LibrarySource } from "./model";

interface SourceDetailDialogProps {
  copy: PrototypeCopy;
  source: LibrarySource;
  opener: HTMLElement | null;
  onClose: () => void;
  onRetry: (sourceId: string) => void;
  onDelete: (sourceId: string) => void;
}

export function SourceDetailDialog({
  copy,
  source,
  opener,
  onClose,
  onRetry,
  onDelete,
}: SourceDetailDialogProps) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const statusLabel =
    source.status === "ready"
      ? copy.library.ready
      : source.status === "failed"
        ? copy.library.failed
        : copy.library.processing;
  const canRetry = source.status === "failed" && source.reviewState !== undefined;

  return (
    <AccessibleDialog
      ariaLabel={`${copy.library.viewSourceButton} ${source.name}`}
      className="tap-catalog-dialog tap-source-detail-dialog"
      onClose={onClose}
      opener={opener}
    >
      <header>
        <h2>{source.name}</h2>
        <Button aria-label={copy.library.close} onClick={onClose}>
          {copy.library.close}
        </Button>
      </header>
      <section>
        <h3>{copy.library.sourceDocuments}</h3>
        <ul className="tap-source-detail-documents">
          <li>
            <span>{source.name}</span>
            <span>{source.type}</span>
            <span data-status={source.status}>{statusLabel}</span>
            {canRetry ? (
              <Button onClick={() => onRetry(source.id)}>
                {copy.library.retryDocument}
              </Button>
            ) : null}
          </li>
        </ul>
      </section>
      <footer>
        {confirmingDelete ? (
          <div className="tap-source-detail-delete-confirm">
            <p>{copy.library.deleteSourceConfirm}</p>
            <div className="tap-dialog-actions">
              <Button onClick={() => setConfirmingDelete(false)}>
                {copy.library.cancel}
              </Button>
              <Button
                danger
                onClick={() => {
                  onDelete(source.id);
                  onClose();
                }}
              >
                {copy.library.confirmDelete}
              </Button>
            </div>
          </div>
        ) : (
          <Button danger onClick={() => setConfirmingDelete(true)}>
            {copy.library.deleteSource}
          </Button>
        )}
      </footer>
    </AccessibleDialog>
  );
}
