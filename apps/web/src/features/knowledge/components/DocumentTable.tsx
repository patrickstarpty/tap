import { Button, Space, Table, Typography, type TableProps } from "antd";
import type { KeyboardEvent, MouseEvent } from "react";

import type { DocumentSummary } from "../api/types";
import { COPY, mediaTypeCopy } from "../copy";
import { DocumentStatus } from "./DocumentStatus";

interface DocumentTableProps {
  documents: DocumentSummary[];
  deletingDocumentId: string | null;
  retryingDocumentId: string | null;
  onOpenDetail: (document: DocumentSummary, row: HTMLTableRowElement) => void;
  onRetry: (document: DocumentSummary) => void;
  onDelete: (document: DocumentSummary, trigger: HTMLElement) => void;
}

function stopRowOpen(event: MouseEvent<HTMLElement>): void {
  event.stopPropagation();
}

function formatUpdatedAt(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

export function DocumentTable({
  documents,
  deletingDocumentId,
  retryingDocumentId,
  onOpenDetail,
  onRetry,
  onDelete,
}: DocumentTableProps) {
  const columns: TableProps<DocumentSummary>["columns"] = [
    {
      title: COPY.filenameColumn,
      dataIndex: "filename",
      key: "filename",
      render: (filename: string) => (
        <Typography.Text
          className="athena-filename"
          ellipsis={{ tooltip: filename }}
        >
          {filename}
        </Typography.Text>
      ),
    },
    {
      title: COPY.typeColumn,
      dataIndex: "mediaType",
      key: "mediaType",
      width: 112,
      render: mediaTypeCopy,
    },
    {
      title: COPY.statusColumn,
      key: "status",
      width: 230,
      render: (_, document) => <DocumentStatus document={document} />,
    },
    {
      title: COPY.chunksColumn,
      dataIndex: "chunkCount",
      key: "chunkCount",
      width: 96,
      align: "right",
    },
    {
      title: COPY.updatedColumn,
      dataIndex: "updatedAt",
      key: "updatedAt",
      width: 178,
      render: formatUpdatedAt,
    },
    {
      title: COPY.actionsColumn,
      key: "actions",
      width: 190,
      fixed: "right",
      render: (_, document) => {
        const unavailable = document.status === "deleting";
        return (
          <Space size={2} onClick={stopRowOpen}>
            <Button
              type="link"
              size="small"
              disabled={unavailable}
              onClick={(event) => {
                const row = event.currentTarget.closest("tr");
                if (row !== null) onOpenDetail(document, row);
              }}
            >
              {COPY.viewDetail}
            </Button>
            {document.status === "failed" ? (
              <Button
                type="link"
                size="small"
                loading={retryingDocumentId === document.documentId}
                disabled={retryingDocumentId === document.documentId}
                onClick={() => onRetry(document)}
              >
                {COPY.retry}
              </Button>
            ) : null}
            <Button
              type="link"
              danger
              size="small"
              aria-label={`${COPY.delete} ${document.filename}`}
              loading={deletingDocumentId === document.documentId}
              disabled={unavailable || deletingDocumentId !== null}
              onClick={(event) => onDelete(document, event.currentTarget)}
            >
              {COPY.delete}
            </Button>
          </Space>
        );
      },
    },
  ];

  const openFromKeyboard = (
    event: KeyboardEvent<HTMLTableRowElement>,
    document: DocumentSummary,
  ) => {
    if (event.target !== event.currentTarget) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    if (document.status === "deleting") return;
    event.preventDefault();
    onOpenDetail(document, event.currentTarget);
  };

  return (
    <Table<DocumentSummary>
      className="athena-document-table"
      columns={columns}
      dataSource={documents}
      rowKey="documentId"
      pagination={false}
      scroll={{ x: 980 }}
      onRow={(document) => ({
        "aria-disabled": document.status === "deleting",
        tabIndex: document.status === "deleting" ? -1 : 0,
        onClick: (event) => {
          if (document.status !== "deleting") {
            onOpenDetail(document, event.currentTarget);
          }
        },
        onKeyDown: (event) => openFromKeyboard(event, document),
      })}
    />
  );
}
