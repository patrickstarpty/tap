import { PlusOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Empty,
  Modal,
  Skeleton,
  Space,
  Tag,
  Typography,
} from "antd";
import { useEffect, useRef, useState } from "react";

import {
  useDeleteDocumentMutation,
  useDocumentListQuery,
  useRetryDocumentMutation,
} from "../api/queries";
import type { DocumentAccepted, DocumentSummary } from "../api/types";
import { COPY, safeProblemCopy } from "../copy";
import { DocumentDetail } from "./DocumentDetail";
import { DocumentTable } from "./DocumentTable";
import { UploadDialog } from "./UploadDialog";

interface DeleteTarget {
  document: DocumentSummary;
  trigger: HTMLElement;
}

export function KnowledgeLibrary({
  pollIntervalMs,
}: { pollIntervalMs?: number } = {}) {
  const addSourceRef = useRef<HTMLButtonElement>(null);
  const detailTriggerRef = useRef<HTMLTableRowElement | null>(null);
  const deleteTriggerRef = useRef<HTMLElement | null>(null);
  const deleteCommittedRef = useRef(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(
    null,
  );
  const [selectedFilename, setSelectedFilename] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [receiptNotice, setReceiptNotice] = useState<string | null>(null);
  const [operationError, setOperationError] = useState<string | null>(null);
  const documentsQuery = useDocumentListQuery({ pollIntervalMs });
  const retryMutation = useRetryDocumentMutation();
  const deleteMutation = useDeleteDocumentMutation();
  const documents = documentsQuery.data?.items ?? [];

  useEffect(() => {
    if (selectedDocumentId === null) return;
    const current = documents.find(
      (document) => document.documentId === selectedDocumentId,
    );
    if (current === undefined || current.status === "deleting") {
      setSelectedDocumentId(null);
    }
  }, [documents, selectedDocumentId]);

  const counts = documents.reduce(
    (result, document) => {
      if (document.status === "ready") result.ready += 1;
      if (document.status === "queued" || document.status === "processing") {
        result.processing += 1;
      }
      if (document.status === "failed") result.failed += 1;
      return result;
    },
    { ready: 0, processing: 0, failed: 0 },
  );

  const openDetail = (document: DocumentSummary, row: HTMLTableRowElement) => {
    detailTriggerRef.current = row;
    setSelectedFilename(document.filename);
    setSelectedDocumentId(document.documentId);
  };

  const handleRetry = (document: DocumentSummary) => {
    setOperationError(null);
    retryMutation.mutate(document.documentId, {
      onError: (error) => setOperationError(safeProblemCopy(error, "action")),
    });
  };

  const handleUploadAccepted = (receipt: DocumentAccepted) => {
    setReceiptNotice(
      receipt.duplicate ? COPY.duplicateReceipt : COPY.uploadAccepted,
    );
  };

  const requestDelete = (document: DocumentSummary, trigger: HTMLElement) => {
    deleteCommittedRef.current = false;
    deleteTriggerRef.current = trigger;
    setDeleteTarget({ document, trigger });
  };

  const confirmDelete = () => {
    if (deleteTarget === null) return;
    const target = deleteTarget;
    deleteCommittedRef.current = true;
    setDeleteTarget(null);
    setOperationError(null);
    if (selectedDocumentId === target.document.documentId) {
      setSelectedDocumentId(null);
    }
    deleteMutation.mutate(target.document.documentId, {
      onSuccess: () => {
        deleteCommittedRef.current = false;
        deleteTriggerRef.current = null;
        queueMicrotask(() => addSourceRef.current?.focus());
      },
      onError: (error) => {
        deleteCommittedRef.current = false;
        deleteTriggerRef.current = null;
        setOperationError(safeProblemCopy(error, "action"));
        queueMicrotask(() => target.trigger.focus());
      },
    });
  };

  return (
    <section
      className="athena-library"
      aria-labelledby="knowledge-library-heading"
    >
      <div className="athena-library-toolbar">
        <div>
          <Typography.Title level={2} id="knowledge-library-heading">
            {COPY.libraryTitle}
          </Typography.Title>
          <Typography.Paragraph type="secondary">
            {COPY.libraryDescription}
          </Typography.Paragraph>
          <Space size={[6, 6]} wrap aria-label="文档状态统计">
            <Tag>{`${COPY.readyCount} ${counts.ready}`}</Tag>
            <Tag>{`${COPY.processingCount} ${counts.processing}`}</Tag>
            <Tag>{`${COPY.failedCount} ${counts.failed}`}</Tag>
          </Space>
        </div>
        <Button
          ref={addSourceRef}
          type="primary"
          icon={<PlusOutlined aria-hidden />}
          onClick={() => setUploadOpen(true)}
        >
          {COPY.addSource}
        </Button>
      </div>

      {receiptNotice !== null ? (
        <Alert
          className="athena-library-alert"
          type="success"
          showIcon
          closable={{ onClose: () => setReceiptNotice(null) }}
          title={receiptNotice}
        />
      ) : null}
      {operationError !== null ? (
        <Alert
          className="athena-library-alert"
          type="error"
          showIcon
          closable={{ onClose: () => setOperationError(null) }}
          title={operationError}
        />
      ) : null}

      {documentsQuery.isPending ? (
        <div className="athena-library-loading" aria-label="正在加载知识库">
          <Skeleton active paragraph={{ rows: 5 }} />
        </div>
      ) : null}
      {documentsQuery.isError ? (
        <Alert
          className="athena-library-alert"
          type="error"
          showIcon
          title={safeProblemCopy(documentsQuery.error, "list")}
          action={
            <Button size="small" onClick={() => void documentsQuery.refetch()}>
              {COPY.retry}
            </Button>
          }
        />
      ) : null}
      {documentsQuery.isSuccess && documents.length === 0 ? (
        <div className="athena-empty-state">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <div>
                <strong>{COPY.emptyTitle}</strong>
                <span>{COPY.emptyDescription}</span>
              </div>
            }
          />
        </div>
      ) : null}
      {documents.length > 0 ? (
        <DocumentTable
          documents={documents}
          retryingDocumentId={
            retryMutation.isPending ? retryMutation.variables : null
          }
          deletingDocumentId={
            deleteMutation.isPending ? deleteMutation.variables : null
          }
          onOpenDetail={openDetail}
          onRetry={handleRetry}
          onDelete={requestDelete}
        />
      ) : null}

      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onAccepted={handleUploadAccepted}
      />
      <DocumentDetail
        documentId={selectedDocumentId}
        filename={selectedFilename}
        onClose={() => setSelectedDocumentId(null)}
        onAfterClose={() => detailTriggerRef.current?.focus()}
      />
      <Modal
        open={deleteTarget !== null}
        title={COPY.deleteTitle}
        destroyOnHidden
        focusable={{ focusTriggerAfterClose: false }}
        onCancel={() => setDeleteTarget(null)}
        afterClose={() => {
          if (!deleteCommittedRef.current) {
            const trigger = deleteTriggerRef.current;
            deleteTriggerRef.current = null;
            trigger?.focus();
          }
        }}
        footer={
          <Space>
            <Button onClick={() => setDeleteTarget(null)}>{COPY.cancel}</Button>
            <Button danger type="primary" onClick={confirmDelete}>
              {COPY.confirmDelete}
            </Button>
          </Space>
        }
      >
        {deleteTarget === null ? null : (
          <p>{COPY.deleteConfirm(deleteTarget.document.filename)}</p>
        )}
      </Modal>
    </section>
  );
}
