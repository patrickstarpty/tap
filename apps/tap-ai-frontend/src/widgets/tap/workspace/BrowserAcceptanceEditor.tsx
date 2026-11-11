import "./BrowserAcceptanceEditor.css";
import {
  BROWSER_READBACK_ATTRIBUTES,
  browserAcceptanceValid,
  type BrowserAcceptanceDraft,
  type BrowserAcceptanceField,
} from "../../../features/workflows/model/browserInput";

export function BrowserAcceptanceEditor({
  draft,
  onChange,
  disabled = false,
}: {
  draft: BrowserAcceptanceDraft;
  onChange: (draft: BrowserAcceptanceDraft) => void;
  disabled?: boolean;
}) {
  const changeField = (index: number, patch: Partial<BrowserAcceptanceField>) =>
    onChange({
      reviewed: false,
      fields: draft.fields.map((field, i) =>
        i === index ? { ...field, ...patch } : field,
      ),
    });
  return (
    <fieldset disabled={disabled} className="tap-browser-acceptance">
      <legend>Required inputs for verification</legend>
      <p>
        Enter the exact business labels and values yourself. These reviewed
        inputs are fixed for this run; freeform input details are not
        independently reviewed or proven. Empty values mean an intentionally
        empty input or readback.
      </p>
      {draft.fields.map((field, index) => (
        <fieldset key={index}>
          <legend>Required input {index + 1}</legend>
          <label>
            Field ID
            <input
              maxLength={80}
              value={field.id}
              onChange={(event) =>
                changeField(index, { id: event.target.value })
              }
              placeholder="customer_name"
            />
          </label>
          <label>
            Input value
            <input
              maxLength={4000}
              value={field.value}
              onChange={(event) =>
                changeField(index, { value: event.target.value })
              }
            />
          </label>
          <label>
            Expected readback value
            <input
              maxLength={4000}
              value={field.readbackValue}
              onChange={(event) =>
                changeField(index, { readbackValue: event.target.value })
              }
            />
          </label>
          <label>
            Readback attribute
            <select
              value={field.attribute}
              onChange={(event) =>
                changeField(index, {
                  attribute: event.target
                    .value as BrowserAcceptanceField["attribute"],
                })
              }
            >
              {BROWSER_READBACK_ATTRIBUTES.map((attribute) => (
                <option key={attribute}>{attribute}</option>
              ))}
            </select>
          </label>
          <label>
            Exact business labels (one per line)
            <textarea
              rows={2}
              value={field.targetTexts.join("\n")}
              onChange={(event) =>
                changeField(index, {
                  targetTexts: event.target.value.split("\n"),
                })
              }
            />
          </label>
          <label>
            Scope text (optional)
            <input
              maxLength={200}
              value={field.scopeText ?? ""}
              onChange={(event) =>
                changeField(index, {
                  scopeText: event.target.value || undefined,
                })
              }
            />
          </label>
          <button
            type="button"
            onClick={() =>
              onChange({
                reviewed: false,
                fields: draft.fields.filter((_, i) => i !== index),
              })
            }
          >
            Remove required input {index + 1}
          </button>
        </fieldset>
      ))}
      <button
        disabled={draft.fields.length >= 20}
        type="button"
        onClick={() =>
          onChange({
            reviewed: false,
            fields: [
              ...draft.fields,
              {
                id: "",
                value: "",
                readbackValue: "",
                attribute: "value",
                targetTexts: [""],
              },
            ],
          })
        }
      >
        Add required input
      </button>
      {draft.fields.length > 0 ? (
        <>
          {!browserAcceptanceValid(draft) ? (
            <p>
              Use up to 20 unique, nonreserved ASCII field IDs (letters, digits,
              underscores or hyphens), values up to 4000 characters, and 1–6
              nonblank business labels up to 200 characters each. Optional scope
              text must be nonblank and up to 200 characters.
            </p>
          ) : null}
          <label>
            <input
              type="checkbox"
              checked={draft.reviewed}
              disabled={!browserAcceptanceValid(draft)}
              onChange={(event) =>
                onChange({ ...draft, reviewed: event.target.checked })
              }
            />
            I reviewed every required input and its expected readback.
          </label>
        </>
      ) : null}
    </fieldset>
  );
}
