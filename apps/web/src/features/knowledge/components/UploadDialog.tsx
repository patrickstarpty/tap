import { InboxOutlined } from "@ant-design/icons";
import { Alert, Button, Modal, Progress, Space, Typography } from "antd";
import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
} from "react";

import { useUploadDocumentMutation } from "../api/queries";
import type { DocumentAccepted } from "../api/types";
import { COPY, safeProblemCopy } from "../copy";

const MAX_FILE_BYTES = 25 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".md", ".markdown", ".txt"];
const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.join(",");

function validationMessage(file: File): string | null {
  const lowerName = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((extension) => lowerName.endsWith(extension))) {
    return COPY.invalidFormat;
  }
  if (file.size > MAX_FILE_BYTES) return COPY.oversizedFile;
  return null;
}

interface UploadDialogProps {
  open: boolean;
  onClose: () => void;
  onAccepted: (receipt: DocumentAccepted) => void;
}

export function UploadDialog({ open, onClose, onAccepted }: UploadDialogProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const uploadMutation = useUploadDocumentMutation();
  const uploadPending = uploadMutation.isPending;

  useEffect(
    () => () => {
      abortControllerRef.current?.abort();
    },
    [],
  );

  useEffect(() => {
    if (!open) return;
    setFile(null);
    setValidationError(null);
    setRequestError(null);
    setProgress(0);
  }, [open]);

  const selectFile = (nextFile: File | undefined) => {
    if (uploadPending) return;
    setRequestError(null);
    setProgress(0);
    if (nextFile === undefined) {
      setFile(null);
      return;
    }
    const error = validationMessage(nextFile);
    setValidationError(error);
    setFile(error === null ? nextFile : null);
  };

  const handleInput = (event: ChangeEvent<HTMLInputElement>) => {
    if (uploadPending) return;
    selectFile(event.target.files?.[0]);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (!uploadPending) selectFile(event.dataTransfer.files[0]);
  };

  const handleDropZoneKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (uploadPending) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    inputRef.current?.click();
  };

  const handleSubmit = async () => {
    if (file === null || uploadPending) return;
    const controller = new AbortController();
    abortControllerRef.current = controller;
    setRequestError(null);
    try {
      const receipt = await uploadMutation.mutateAsync({
        file,
        onProgress: setProgress,
        signal: controller.signal,
      });
      onAccepted(receipt);
      onClose();
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setRequestError(safeProblemCopy(error, "upload"));
      }
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
      }
    }
  };

  return (
    <Modal
      open={open}
      title={COPY.uploadTitle}
      onCancel={uploadMutation.isPending ? undefined : onClose}
      mask={{ closable: !uploadMutation.isPending }}
      keyboard={!uploadMutation.isPending}
      closable={!uploadMutation.isPending}
      destroyOnHidden
      footer={
        <Space>
          <Button onClick={onClose} disabled={uploadMutation.isPending}>
            {COPY.cancel}
          </Button>
          <Button
            type="primary"
            onClick={() => void handleSubmit()}
            disabled={file === null || uploadMutation.isPending}
            loading={uploadMutation.isPending}
          >
            {COPY.startUpload}
          </Button>
        </Space>
      }
    >
      <div
        className="athena-drop-zone"
        role="button"
        tabIndex={uploadPending ? -1 : 0}
        aria-disabled={uploadPending}
        aria-label={COPY.dropZoneLabel}
        onClick={() => {
          if (!uploadPending) inputRef.current?.click();
        }}
        onKeyDown={handleDropZoneKey}
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
      >
        <InboxOutlined aria-hidden />
        <Typography.Text strong>{COPY.dropTitle}</Typography.Text>
        <Typography.Text type="secondary">
          {COPY.dropDescription}
        </Typography.Text>
        <Typography.Text type="secondary" className="athena-drop-formats">
          {COPY.acceptedFormats}
        </Typography.Text>
      </div>
      <input
        ref={inputRef}
        className="athena-visually-hidden"
        type="file"
        accept={ACCEPT_ATTRIBUTE}
        tabIndex={-1}
        disabled={uploadPending}
        aria-label={COPY.chooseDocument}
        onChange={handleInput}
      />

      {file !== null ? (
        <div className="athena-selected-file">
          <span>{COPY.selectedFile}</span>
          <strong>{file.name}</strong>
        </div>
      ) : null}
      {validationError !== null ? (
        <Alert
          className="athena-dialog-alert"
          type="warning"
          showIcon
          title={validationError}
        />
      ) : null}
      {requestError !== null ? (
        <Alert
          className="athena-dialog-alert"
          type="error"
          showIcon
          title={requestError}
        />
      ) : null}
      {uploadMutation.isPending ? (
        <div className="athena-upload-progress" aria-live="polite">
          <span>{COPY.uploadProgress(Math.round(progress * 100))}</span>
          <Progress
            percent={Math.round(progress * 100)}
            showInfo={false}
            size="small"
          />
        </div>
      ) : null}
    </Modal>
  );
}
