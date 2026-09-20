import { useEffect, useState } from "react";
import { api, post } from "../api";
import { Notice } from "../Components/Notice";
import "../styles/recovery.css";

export default function RecoveryEmail() {
  const [details, setDetails] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    setError("");
    api("/auth/email")
      .then((data) => {
        if (active) {
          setDetails(data);
          setEmail(data.pending_email || data.email || "");
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [attempt]);
  const save = async (event, remove = false) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (remove) {
        await api("/auth/email", {
          method: "DELETE",
          body: JSON.stringify({ current_password: password }),
        });
        setEmail("");
        setMessage("Recovery email removed.");
      } else {
        await post("/auth/email", { email, current_password: password });
        setMessage(
          "Check your email for a verification link. Your existing recovery email stays active until the new address is verified.",
        );
      }
      setPassword("");
      setDetails(await api("/auth/email"));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="form-panel account-card recovery-email" onSubmit={save}>
      <h3>Recovery email</h3>
      <p className="hint">
        Optional. Used only to verify your address and help you reset your
        password.
      </p>
      <Notice error>{error}</Notice>
      <Notice>{message}</Notice>
      {!details && error && (
        <button
          type="button"
          className="button"
          onClick={() => setAttempt((value) => value + 1)}
        >
          Retry
        </button>
      )}
      {details ? (
        <>
          {details.email && <p>Verified: {details.email}</p>}
          {details.pending_email && (
            <p className="hint">
              Awaiting verification: {details.pending_email}
            </p>
          )}
          {!details.available && (
            <p className="hint">
              Email delivery is not available yet. You can still change your
              password above.
            </p>
          )}
          <fieldset disabled={busy}>
            <label className="field">
              Email address
              <input
                type="email"
                autoComplete="email"
                maxLength={254}
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={!details.available}
              />
            </label>
            <label className="field">
              Current password for recovery email
              <input
                type="password"
                autoComplete="current-password"
                required
                maxLength={128}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            <div className="actions">
              <button className="button" disabled={!details.available}>
                {busy ? "Please wait…" : "Verify email"}
              </button>
              {(details.email || details.pending_email) && (
                <button
                  type="button"
                  className="text-button"
                  disabled={!password}
                  onClick={(e) => save(e, true)}
                >
                  Remove email
                </button>
              )}
            </div>
          </fieldset>
        </>
      ) : (
        !error && <p role="status">Loading recovery settings…</p>
      )}
    </form>
  );
}
