import { useMemo, useState } from "react";

export default function SelectionPattern({
  total,
  busy,
  onApply,
  onClose,
  noun = "links",
}) {
  const [every, setEvery] = useState("2"),
    [starting, setStarting] = useState("1");
  const [first, setFirst] = useState("1"),
    [last, setLast] = useState("");
  const [mode, setMode] = useState("replace");
  const pattern = useMemo(
    () => ({
      every: Number(every),
      starting: Number(starting),
      first: Number(first),
      last: last === "" ? null : Number(last),
    }),
    [every, starting, first, last],
  );
  const valid =
    Number.isInteger(pattern.every) &&
    pattern.every >= 1 &&
    pattern.every <= 4999 &&
    Number.isInteger(pattern.starting) &&
    pattern.starting >= 1 &&
    pattern.starting <= pattern.every &&
    Number.isInteger(pattern.first) &&
    pattern.first >= 1 &&
    pattern.first <= 4999 &&
    (pattern.last === null ||
      (Number.isInteger(pattern.last) &&
        pattern.last >= pattern.first &&
        pattern.last <= 4999));
  const matches = useMemo(
    () =>
      valid
        ? Array.from({ length: total }, (_, index) => index + 1).filter(
            (position) =>
              position >= pattern.first &&
              position <= (pattern.last ?? total) &&
              (position - pattern.starting) % pattern.every === 0,
          )
        : [],
    [pattern, total, valid],
  );
  return (
    <form
      className="selection-pattern"
      aria-label="Select by pattern"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) onApply(pattern, mode);
      }}
    >
      <div className="pattern-fields">
        <label>
          Every{" "}
          <input
            type="number"
            aria-label="Select every"
            min="1"
            max="4999"
            required
            value={every}
            onChange={(e) => setEvery(e.target.value)}
          />{" "}
          {noun}
        </label>
        <label>
          Starting with{" "}
          <input
            type="number"
            aria-label="Starting with position"
            min="1"
            max={every || 4999}
            required
            value={starting}
            onChange={(e) => setStarting(e.target.value)}
          />
        </label>
        <label>
          Within positions{" "}
          <input
            type="number"
            aria-label="From position"
            min="1"
            max="4999"
            required
            value={first}
            onChange={(e) => setFirst(e.target.value)}
          />
        </label>
        <label>
          to{" "}
          <input
            type="number"
            aria-label="Through position"
            min={first || 1}
            max="4999"
            placeholder={String(total)}
            value={last}
            onChange={(e) => setLast(e.target.value)}
          />
        </label>
        <select
          aria-label="Apply pattern to selection"
          value={mode}
          onChange={(e) => setMode(e.target.value)}
        >
          <option value="replace">Replace selection</option>
          <option value="add">Add to selection</option>
          <option value="remove">Remove from selection</option>
        </select>
      </div>
      <p className="hint">
        Positions follow the current filtered order across all pages. Every 1
        selects each {noun === "items" ? "item" : "link"}.
      </p>
      <p className="pattern-preview" aria-live="polite">
        {valid
          ? `${matches.length} matches${matches.length ? `: ${matches.slice(0, 8).join(", ")}${matches.length > 8 ? "…" : ""}` : ""}`
          : "Choose a starting position within the interval and a valid range."}
      </p>
      <div className="actions">
        <button className="button" type="submit" disabled={busy || !valid}>
          Apply selection
        </button>
        <button className="text-button" type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </form>
  );
}
