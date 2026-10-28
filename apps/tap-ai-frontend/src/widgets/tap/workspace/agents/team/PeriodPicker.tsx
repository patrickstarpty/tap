import { useMemo, useState } from "react";

import {
  resolvePeriod,
  type PeriodPreset,
  type TeamPeriod,
} from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";

export interface PeriodSelection {
  preset: PeriodPreset;
  custom: TeamPeriod;
}

export interface PeriodPickerProps {
  copy: WorkspaceCopy;
  value: PeriodSelection;
  onChange: (value: PeriodSelection) => void;
}

// A date input reports partial years ("0002-10-18") while the user types.
const COMPLETE_DATE = /^[1-9]\d{3}-\d{2}-\d{2}$/;

/** True while a custom range cannot be queried: incomplete or inverted. */
export function selectionInvalid(selection: PeriodSelection): boolean {
  if (selection.preset !== "custom") return false;
  const { from, to } = selection.custom;
  return !COMPLETE_DATE.test(from) || !COMPLETE_DATE.test(to) || from > to;
}

/** The period to query: the selection while it is valid, otherwise the last
 * valid one, so an in-progress edit never fires a request and the previous
 * result stays on screen. */
export function useValidPeriod(selection: PeriodSelection): {
  period: TeamPeriod;
  invalid: boolean;
} {
  const candidate = useMemo(
    () => resolvePeriod(selection.preset, new Date(), selection.custom),
    [selection],
  );
  const invalid = selectionInvalid(selection);
  const [lastValid, setLastValid] = useState(candidate);
  if (
    !invalid &&
    (lastValid.from !== candidate.from || lastValid.to !== candidate.to)
  )
    setLastValid(candidate);
  return { period: invalid ? lastValid : candidate, invalid };
}

const PRESETS: PeriodPreset[] = [
  "last7",
  "last30",
  "thisMonth",
  "lastMonth",
  "custom",
];

export function PeriodPicker({ copy, value, onChange }: PeriodPickerProps) {
  const text = copy.agentTeam.period;
  return (
    <div className="tap-team-period">
      <label>
        <span>{text.label}</span>
        <select
          value={value.preset}
          onChange={(event) =>
            onChange({ ...value, preset: event.target.value as PeriodPreset })
          }
        >
          {PRESETS.map((preset) => (
            <option key={preset} value={preset}>
              {text[preset]}
            </option>
          ))}
        </select>
      </label>
      {value.preset === "custom" ? (
        <>
          <label>
            <span>{text.from}</span>
            <input
              type="date"
              value={value.custom.from}
              onChange={(event) =>
                event.target.value !== "" &&
                onChange({
                  ...value,
                  custom: { ...value.custom, from: event.target.value },
                })
              }
            />
          </label>
          <label>
            <span>{text.to}</span>
            <input
              type="date"
              value={value.custom.to}
              onChange={(event) =>
                event.target.value !== "" &&
                onChange({
                  ...value,
                  custom: { ...value.custom, to: event.target.value },
                })
              }
            />
          </label>
        </>
      ) : null}
      {value.preset === "custom" && value.custom.from > value.custom.to ? (
        <p role="alert">{text.invalidRange}</p>
      ) : null}
    </div>
  );
}
