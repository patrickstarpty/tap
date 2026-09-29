import { useState } from "react";
import { Button } from "antd";
import type { PrototypeCopy } from "./copy";
import {
  SAMPLE_CHUNK_SOURCE_TEXT,
  generateChunks,
  isValidChunkSettings,
  type Chunk,
  type ChunkSettings,
} from "./ChunkManager";

export function UploadChunkSettings({
  copy,
  value,
  onChange,
}: {
  copy: PrototypeCopy;
  value: ChunkSettings;
  onChange: (value: ChunkSettings) => void;
}) {
  const [preview, setPreview] = useState<Chunk[] | null>(null);

  const patch = <K extends keyof ChunkSettings>(
    key: K,
    patchValue: ChunkSettings[K],
  ) => {
    onChange({ ...value, [key]: patchValue });
    setPreview(null);
  };

  return (
    <div className="tap-upload-chunk-settings">
      <fieldset>
        <legend>{copy.library.chunkMode}</legend>
        <label>
          <input
            type="radio"
            name="tap-upload-chunk-mode"
            value="general"
            checked={value.mode === "general"}
            onChange={() => patch("mode", "general")}
          />
          {copy.library.chunkModeGeneral}
        </label>
        <label>
          <input
            type="radio"
            name="tap-upload-chunk-mode"
            value="parent-child"
            checked={value.mode === "parent-child"}
            onChange={() => patch("mode", "parent-child")}
          />
          {copy.library.chunkModeParentChild}
        </label>
      </fieldset>
      <label>
        {copy.library.chunkMax}
        <input
          type="number"
          min={1}
          max={10000}
          value={value.max}
          onChange={(event) => patch("max", Number(event.target.value))}
        />
      </label>
      <label>
        {copy.library.chunkOverlap}
        <input
          type="number"
          min={0}
          max={Math.max(0, value.max - 1)}
          value={value.overlap}
          onChange={(event) => patch("overlap", Number(event.target.value))}
        />
      </label>
      {value.mode === "parent-child" ? (
        <label>
          {copy.library.chunkChildMax}
          <input
            type="number"
            min={1}
            max={10000}
            value={value.childMax}
            onChange={(event) =>
              patch("childMax", Number(event.target.value))
            }
          />
        </label>
      ) : null}
      <Button
        htmlType="button"
        disabled={!isValidChunkSettings(value)}
        onClick={() =>
          setPreview(
            generateChunks(SAMPLE_CHUNK_SOURCE_TEXT, value).slice(0, 3),
          )
        }
      >
        {copy.library.previewChunks}
      </Button>
      {preview ? (
        <ul role="list" className="tap-upload-chunk-preview">
          {preview.map((chunk) => (
            <li key={chunk.id} className="tap-upload-chunk-preview-item">
              <p>{chunk.content}</p>
              {value.mode === "parent-child" ? (
                <span>
                  {copy.library.childChunks.replace(
                    "{n}",
                    String(chunk.children.length),
                  )}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
