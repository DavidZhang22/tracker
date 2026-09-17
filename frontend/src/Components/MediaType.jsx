import { mediaLabel, mediaTypes } from "../media";

export default function MediaType({ value, detected, onChange, disabled }) {
  return (
    <label className="field">
      Media type
      <select
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      >
        <option value="">Automatic ({mediaLabel(detected)})</option>
        {mediaTypes.map(([kind, label]) => (
          <option key={kind} value={kind}>
            {label}
          </option>
        ))}
      </select>
    </label>
  );
}
