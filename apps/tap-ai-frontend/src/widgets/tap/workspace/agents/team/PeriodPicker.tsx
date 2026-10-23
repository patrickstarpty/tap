import type {
  PeriodPreset,
  TeamPeriod,
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
              max={value.custom.to}
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
              min={value.custom.from}
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
    </div>
  );
}
