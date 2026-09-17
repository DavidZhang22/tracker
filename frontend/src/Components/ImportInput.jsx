import { useId, useState } from "react";
import { UploadIcon, DocumentTextIcon } from "@heroicons/react/outline";
import { Icon } from "./Icons";
import { uploadFile } from "../api";

export function ImportDetails({ context }) {
  return context ? (
    <details className="csv-details">
      <summary>Details</summary>
      <p>{context}</p>
    </details>
  ) : null;
}

export default function ImportInput({
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
  const fileId = useId();
  const [inputMode, setInputMode] = useState("file");
  const [text, setText] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const hasInput = inputMode === "file" ? Boolean(file) : Boolean(text.trim());
  const [metadata, setMetadata] = useState(null);
  const csv =
    Boolean(metadata) ||
    (inputMode === "file" && /\.(csv|tsv)$/i.test(file?.name || ""));
  const [options, setOptions] = useState({});
  const [busy, setBusy] = useState(false);
  const change = (field, value) => {
    setOptions((old) => ({ ...old, [field]: value }));
    onInvalidate();
  };
  const preview = async (event) => {
    event.preventDefault();
    if (!hasInput || busy || disabled) return;
    setBusy(true);
    onBusy(true);
    onInvalidate();
    onError("");
    try {
      const source =
        inputMode === "file"
          ? file
          : new File([text], "Pasted links.txt", { type: "text/plain" });
      const result = await uploadFile(source, {
        ...options,
        keywords,
        ...(itemId ? { item_id: itemId } : {}),
      });
      setMetadata(result.csv);
      setReviewed(true);
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
      <div className="source-tabs" role="group" aria-label="Import input">
        {[
          ["file", "Upload file"],
          ["text", "Paste text"],
        ].map(([value, label]) => (
          <button
            type="button"
            className={`button ${inputMode === value ? "primary" : ""}`}
            key={value}
            aria-pressed={inputMode === value}
            disabled={disabled || busy}
            onClick={() => {
              setInputMode(value);
              setMetadata(null);
              setOptions({});
              setReviewed(false);
              onInvalidate();
              onError("");
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {inputMode === "file" ? (
        <div className="field">
          <label htmlFor={fileId}>File</label>
          <div
            className={`file-picker ${disabled || busy ? "is-disabled" : ""}`}
          >
            <input
              id={fileId}
              className="file-picker-input"
              type="file"
              aria-label="Import file"
              aria-describedby={`${fileId}-help ${fileId}-name`}
              accept=".csv,.tsv,.txt,.md,.html,.htm,.pdf,.xlsx,.pptx,.docx"
              disabled={disabled || busy}
              onChange={(event) => {
                const next = event.target.files?.[0];
                setMetadata(null);
                setOptions({});
                setReviewed(false);
                onInvalidate();
                onError("");
                if (next && (!next.size || next.size > 4000000)) {
                  setFile(null);
                  onError("Choose a non-empty file up to 4 MB.");
                } else setFile(next || null);
              }}
            />
            <label htmlFor={fileId} className="file-picker-trigger">
              <Icon as={file ? DocumentTextIcon : UploadIcon} />
              <span>{file ? "Change file" : "Choose file"}</span>
            </label>
            <span
              className="file-picker-name"
              id={`${fileId}-name`}
              aria-live="polite"
            >
              {file ? file.name : "No file selected"}
              {file && (
                <small>
                  {file.size < 1000
                    ? `${file.size} bytes`
                    : file.size < 1000000
                      ? `${(file.size / 1000).toFixed(1)} KB`
                      : `${(file.size / 1000000).toFixed(2)} MB`}
                </small>
              )}
            </span>
          </div>
          <span className="hint" id={`${fileId}-help`}>
            CSV, Excel (.xlsx), PowerPoint (.pptx), Word (.docx), PDF, HTML,
            Markdown, or text. Up to 4 MB.
          </span>
        </div>
      ) : (
        <label className="field">
          Paste text
          <textarea
            aria-label="Paste text"
            rows={8}
            maxLength={200000}
            value={text}
            disabled={disabled || busy}
            placeholder="Paste links with their titles, dates, or other details."
            onChange={(event) => {
              setText(event.target.value);
              setMetadata(null);
              setOptions({});
              setReviewed(false);
              onInvalidate();
            }}
          />
          <span className="hint">
            Up to 200,000 characters. Include complete https:// links.
          </span>
        </label>
      )}
      <details className="csv-columns" open={metadata ? true : undefined}>
        <summary>{metadata ? "Review columns" : "Import options"}</summary>
        <div className="csv-field-grid">
          <label className="field csv-keywords">
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
              Match every comma-separated keyword in a record.
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
          {csv && (
            <>
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
            </>
          )}
          {!csv && (
            <label className="field">
              Keep
              <select
                aria-label="Links to import"
                value={options.link_filter || "all"}
                disabled={disabled || busy}
                onChange={(event) => change("link_filter", event.target.value)}
              >
                <option value="all">All links</option>
                <option value="content">Content links (model filtered)</option>
              </select>
            </label>
          )}
        </div>
      </details>
      <button
        type="submit"
        className="button primary"
        disabled={!hasInput || busy || disabled}
      >
        {busy
          ? "Reading input…"
          : reviewed
            ? "Update preview"
            : "Preview links"}
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
