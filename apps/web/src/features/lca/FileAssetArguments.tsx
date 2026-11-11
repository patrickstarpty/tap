import { useEffect, useRef, useState } from "react";
import {
  LcaHttpError,
  type LcaAsset,
  type LcaClient,
  type LcaStep,
} from "./client";

const MAX_FILE_BYTES = 256000;
const readFile = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const encoded = String(reader.result ?? "").split(",")[1];
      if (encoded) resolve(encoded);
      else reject(new Error("The selected file could not be read."));
    };
    reader.onerror = () =>
      reject(new Error("The selected file could not be read."));
    reader.readAsDataURL(file);
  });

export function FileAssetArguments({
  step,
  projectId,
  client,
  onChange,
}: {
  step: LcaStep;
  projectId?: string;
  client?: LcaClient;
  onChange(step: LcaStep): void;
}) {
  const [assets, setAssets] = useState<LcaAsset[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [intent, setIntent] = useState<{
    key: string;
    body: { filename: string; content_base64: string };
  } | null>(null);
  const mounted = useRef(true);
  const lock = useRef(false);
  const latest = useRef({ step, onChange });
  latest.current = { step, onChange };
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    let current = true;
    if (!client || !projectId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    void client
      .listAssets(projectId)
      .then((next) => {
        if (current) setAssets(next);
      })
      .catch(() => {
        if (current)
          setError(
            "Project files could not be loaded. Refresh the file list to retry.",
          );
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [client, projectId, refresh]);
  const select = (asset: LcaAsset) =>
    latest.current.onChange({
      ...latest.current.step,
      arguments: { asset_id: asset.id, sha256: asset.sha256 },
    });
  const upload = async () => {
    if (lock.current || loading || !client || !projectId || (!file && !intent))
      return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const next = intent ?? {
        key: crypto.randomUUID(),
        body: { filename: file!.name, content_base64: await readFile(file!) },
      };
      if (!mounted.current) return;
      setIntent(next);
      const asset = await client.uploadAsset(projectId, next.body, next.key);
      if (!mounted.current) return;
      setAssets((old) => [
        asset,
        ...old.filter((item) => item.id !== asset.id),
      ]);
      select(asset);
      setIntent(null);
      setFile(null);
    } catch (cause) {
      if (mounted.current) {
        if (cause instanceof LcaHttpError && cause.status < 500)
          setIntent(null);
        setError(
          cause instanceof LcaHttpError
            ? `${cause.message} The selected file has not been bound to this action.`
            : "Upload could not be confirmed. Retry the same file request to reconcile it.",
        );
      }
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  const selected = assets.find((asset) => asset.id === step.arguments.asset_id);
  const unavailable = !loading && step.arguments.asset_id && !selected;
  const digestChanged = selected && selected.sha256 !== step.arguments.sha256;
  if (!client || !projectId)
    return (
      <p role="alert">
        Open this flow in its project to select a project file.
      </p>
    );
  return (
    <section className="lca-file-assets" aria-label="Project file input">
      <p>
        Choose a project file, or explicitly upload a synthetic, non-secret file
        (1–256,000 bytes). Replay sends that exact file to the target input.
        Local paths are never accepted.
      </p>
      <label>
        Project file
        <select
          aria-label={`Step ${step.id} project file`}
          value={String(step.arguments.asset_id ?? "")}
          disabled={loading || busy}
          onChange={(event) => {
            const asset = assets.find((item) => item.id === event.target.value);
            if (asset) select(asset);
          }}
        >
          <option value="" disabled>
            Choose a project file
          </option>
          {assets.map((asset) => (
            <option key={asset.id} value={asset.id}>
              {asset.filename} ({asset.size_bytes} bytes)
            </option>
          ))}
          {step.arguments.asset_id && !selected && (
            <option value={String(step.arguments.asset_id)}>
              {String(step.arguments.asset_id)} (not in loaded files)
            </option>
          )}
        </select>
      </label>
      {loading ? (
        <p role="status">Loading project files…</p>
      ) : (
        !assets.length && <p>No project files loaded.</p>
      )}
      {selected && (
        <p>
          File type: {selected.content_type} · {selected.size_bytes} bytes
        </p>
      )}
      {step.arguments.sha256 && (
        <p className="lca-asset-digest">
          SHA-256: {String(step.arguments.sha256)}
        </p>
      )}
      <button
        type="button"
        disabled={loading || busy}
        onClick={() => setRefresh((value) => value + 1)}
      >
        Refresh project files
      </button>
      <label>
        Choose project file
        <input
          type="file"
          disabled={busy || !!intent}
          onChange={(event) => {
            const selectedFile = event.target.files?.[0];
            setError("");
            setFile(null);
            if (!selectedFile) return;
            if (selectedFile.size === 0)
              setError(
                "The selected file is empty. Choose a file with 1–256,000 bytes.",
              );
            else if (selectedFile.size > MAX_FILE_BYTES)
              setError(
                "The selected file exceeds 256,000 bytes. Choose a smaller file.",
              );
            else setFile(selectedFile);
          }}
        />
      </label>
      {file && (
        <p>
          {file.name} · {file.size} bytes selected
        </p>
      )}
      <button
        type="button"
        disabled={loading || busy || (!file && !intent)}
        onClick={() => void upload()}
      >
        {busy
          ? "Uploading file…"
          : intent
            ? "Reconcile file upload"
            : "Upload selected file"}
      </button>
      {error && <p role="alert">{error}</p>}
      {!error && unavailable && (
        <p role="alert">
          The bound file is not in the loaded project files. Its binding is
          preserved. Refresh or explicitly select a replacement.
        </p>
      )}
      {!error && digestChanged && (
        <p role="alert">
          The bound digest differs from the selected project file. Its original
          digest is preserved. Explicitly select the file to replace the
          binding.
        </p>
      )}
    </section>
  );
}
