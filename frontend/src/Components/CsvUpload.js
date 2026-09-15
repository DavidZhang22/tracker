import { useState } from "react";
import { uploadCsv } from "../api";

export function CsvDetails({ context }) {
  return context ? (
    <details className="csv-details">
      <summary>Details</summary>
      <p>{context}</p>
    </details>
  ) : null;
}

export default function CsvUpload({
  disabled,
  itemId,
  keywords,
  onKeywords,
  onPreview,
  onBusy,
  onError,
  onInvalidate,
}) {
  const [file, setFile] = useState(null);
  const [metadata, setMetadata] = useState(null);
  const [options, setOptions] = useState({});
  const [busy, setBusy] = useState(false);
  const change = (field, value) => {
    setOptions((old) => ({ ...old, [field]: value }));
    onInvalidate();
  };
  const preview = async (event) => {
    event.preventDefault();
    if (!file || busy || disabled) return;
    setBusy(true);
    onBusy(true);
    onInvalidate();
    onError("");
    try {
      const result = await uploadCsv(file, {
        ...options,
        keywords,
        ...(itemId ? { item_id: itemId } : {}),
      });
      setMetadata(result.csv);
      onPreview(result);
    } catch (error) {
      onError(error.message);
    } finally {
      setBusy(false);
      onBusy(false);
    }
  };
  return (
    <form className="form-panel" onSubmit={preview}>
      <label className="field">
        CSV file
        <input
          type="file"
          aria-label="CSV file"
          accept=".csv,.tsv,text/csv,text/tab-separated-values"
          disabled={disabled || busy}
          onChange={(event) => {
            const next = event.target.files?.[0];
            setMetadata(null);
            setOptions({});
            onInvalidate();
            onError("");
            if (next && (!next.size || next.size > 4000000)) {
              setFile(null);
              onError("Choose a non-empty CSV file up to 4 MB.");
            } else setFile(next || null);
          }}
        />
        <span className="hint">
          Each row becomes a link in this item. Up to 4 MB and 4,999 unique
          links.
        </span>
      </label>
      <details className="csv-columns" open={metadata ? true : undefined}>
        <summary>{metadata ? "Review columns" : "File options"}</summary>
        <div className="csv-field-grid">
          <label className="field">
            Keywords
            <input
              value={keywords}
              maxLength={300}
              disabled={disabled || busy}
              placeholder="Optional: remote, Python"
              onChange={(event) => {
                onKeywords(event.target.value);
                onInvalidate();
              }}
            />
            <span className="hint">
              Match every comma-separated keyword in a row.
            </span>
          </label>
          {metadata &&
            [
              ["url", "Link column"],
              ["title", "Title column"],
              ["company", "Company / creator column"],
              ["date", "Date column"],
              ["number", "Number column"],
            ].map(([field, label]) => (
              <label className="field" key={field}>
                {label}
                <select
                  disabled={disabled || busy}
                  value={options[`${field}_column`] ?? metadata.selected[field]}
                  onChange={(event) =>
                    change(`${field}_column`, Number(event.target.value))
                  }
                >
                  {field !== "url" && <option value={-1}>None</option>}
                  {metadata.columns.map((column) => (
                    <option key={column.index} value={column.index}>
                      {column.index + 1}. {column.label}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          <label className="field">
            First row
            <select
              disabled={disabled || busy}
              value={options.header || "auto"}
              onChange={(event) => {
                setOptions((old) => ({
                  header: event.target.value,
                  delimiter: old.delimiter || "auto",
                  date_order: old.date_order || "auto",
                }));
                setMetadata(null);
                onInvalidate();
              }}
            >
              <option value="auto">Detect headings</option>
              <option value="yes">Column headings</option>
              <option value="no">Data</option>
            </select>
          </label>
          <label className="field">
            Separator
            <select
              disabled={disabled || busy}
              value={options.delimiter || "auto"}
              onChange={(event) => {
                setOptions((old) => ({
                  delimiter: event.target.value,
                  header: old.header || "auto",
                  date_order: old.date_order || "auto",
                }));
                setMetadata(null);
                onInvalidate();
              }}
            >
              <option value="auto">Detect separator</option>
              <option value=",">Comma</option>
              <option value=";">Semicolon</option>
              <option value={"\t"}>Tab</option>
              <option value="|">Pipe</option>
            </select>
          </label>
          <label className="field">
            Numeric dates
            <select
              disabled={disabled || busy}
              value={options.date_order || "auto"}
              onChange={(event) => change("date_order", event.target.value)}
            >
              <option value="auto">Leave ambiguous dates unset</option>
              <option value="month_first">Month / day / year</option>
              <option value="day_first">Day / month / year</option>
            </select>
          </label>
        </div>
      </details>
      <button
        type="submit"
        className="button primary"
        disabled={!file || busy || disabled}
      >
        {busy ? "Reading CSV…" : metadata ? "Update preview" : "Preview CSV"}
      </button>
      {itemId && (
        <p className="hint">
          Matching links will update. Read, favorite, muted, and deleted states
          are kept. Links missing from the file stay in the item.
        </p>
      )}
    </form>
  );
}
