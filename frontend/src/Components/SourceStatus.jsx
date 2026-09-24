import { Link } from "react-router-dom";
import { Notice } from "./Notice";
import "../styles/source-status.css";

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

export default function SourceStatus({ status }) {
  if (!status?.message || status.status === "accessible") return null;
  const alternatives = (status.alternatives || [])
    .filter((item) => item.relationship === "official" && safeUrl(item.url))
    .slice(0, 5);
  const checked = new Date(status.checked_at);
  return (
    <Notice resetKey={`${status.host}:${status.checked_at}`}>
      <div className="source-status">
        <p>{status.message}</p>
        {alternatives.length > 0 && (
          <>
            <ul>
              {alternatives.map((item) => (
                <li key={item.url}>
                  <Link to={`/add?url=${encodeURIComponent(item.url)}`}>
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
            <p className="hint">
              Official alternatives. Coverage may differ from your original
              page. Review the links before saving.
            </p>
          </>
        )}
        <p className="hint">
          {Number.isFinite(checked.getTime()) && (
            <>Checked {checked.toLocaleDateString()}. </>
          )}
          {status.stale
            ? "This check is older than a day. Availability may have changed."
            : "Other pages on this site may still work."}
        </p>
      </div>
    </Notice>
  );
}
