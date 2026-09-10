import { useEffect, useState } from "react";
import { XIcon } from "@heroicons/react/outline";

export function Notice({ children, error = false, resetKey }) {
  const [dismissedKey, setDismissedKey] = useState(null);
  const messageKey =
    resetKey ?? (typeof children === "string" ? children : Boolean(children));
  useEffect(() => {
    if (!children) setDismissedKey(null);
  }, [children]);
  if (!children || dismissedKey === messageKey) return null;
  return (
    <div
      className={`notice ${error ? "error" : ""}`}
      role={error ? "alert" : "status"}
    >
      <div className="notice-content">{children}</div>
      <button
        type="button"
        className="icon-button notice-dismiss"
        aria-label={error ? "Dismiss error" : "Dismiss message"}
        onClick={() => setDismissedKey(messageKey)}
      >
        <XIcon className="icon" aria-hidden="true" />
      </button>
    </div>
  );
}

export function ScanNote({ item }) {
  const [dismissedKey, setDismissedKey] = useState(null);
  const signature = JSON.stringify([item.id, item.error, item.warnings]);
  if (dismissedKey === signature || !item.error) return null;
  return (
    <div className="row-scan-note">
      <details>
        <summary>Last check incomplete</summary>
        <p>{item.error} Saved links are still available.</p>
      </details>
      <button
        type="button"
        className="icon-button"
        aria-label={`Dismiss scan notes for ${item.title}`}
        onClick={() => setDismissedKey(signature)}
      >
        <XIcon className="icon" aria-hidden="true" />
      </button>
    </div>
  );
}
