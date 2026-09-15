import { useState } from "react";
import { post } from "../api";
import { Notice } from "../Components/Notice";

export default function AccountData({ auth }) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const perform = async (action) => {
    if (!password) {
      setError("Enter your current password to continue.");
      return;
    }
    if (action === "delete" && confirmation !== "DELETE") return;
    setBusy(action);
    setError("");
    setMessage("");
    try {
      const result = await post(`/auth/${action}`, {
        current_password: password,
        ...(action === "delete" ? { confirmation } : {}),
      });
      setPassword("");
      if (action === "export") {
        const url = URL.createObjectURL(
          new Blob([JSON.stringify(result, null, 2)], {
            type: "application/json",
          }),
        );
        const link = document.createElement("a");
        link.href = url;
        link.download = "trackify-export.json";
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        setMessage("Your data download is ready. Keep it private.");
      } else {
        auth.endSession(
          action === "delete"
            ? result.cleanup_pending
              ? "Your account is closed. Remaining data cleanup is queued and will be retried."
              : "Your account and active library have been deleted. Managed backup cleanup follows the privacy notice."
            : "All sessions have been signed out.",
        );
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };
  return (
    <section className="form-panel account-data">
      <h3>Your data &amp; sessions</h3>
      <p className="hint">
        Confirm your password to download your data, sign out everywhere, or
        close your account.
      </p>
      <Notice error>{error}</Notice>
      <Notice>{message}</Notice>
      <fieldset disabled={Boolean(busy)}>
        <label className="field">
          Password for account controls
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            maxLength={128}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <div className="actions">
          <button className="button" onClick={() => perform("export")}>
            {busy === "export" ? "Preparing…" : "Download my data"}
          </button>
          <button className="button" onClick={() => perform("logout-all")}>
            Sign out all sessions
          </button>
        </div>
        <details className="account-delete">
          <summary>Delete account</summary>
          <p>
            This permanently removes your account, library, reading progress,
            settings and Trash. All sessions are signed out. This cannot be
            undone. Download your data first if you want a copy.
          </p>
          <label className="field">
            Type DELETE to confirm
            <input
              value={confirmation}
              autoComplete="off"
              maxLength={6}
              onChange={(e) => setConfirmation(e.target.value)}
            />
          </label>
          <button
            className="button danger"
            disabled={confirmation !== "DELETE" || !password}
            onClick={() => perform("delete")}
          >
            {busy === "delete" ? "Deleting…" : "Permanently delete my account"}
          </button>
        </details>
      </fieldset>
      <p className="hint">
        <a href="/privacy">Privacy and data rights</a> ·{" "}
        <a href="/terms">Terms</a>
      </p>
    </section>
  );
}
