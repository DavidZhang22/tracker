import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, post } from "../api";
import { Notice } from "../Components/Notice";
import "../styles/recovery.css";

export default function RecoveryPage({ verify = false }) {
  const [token, setToken] = useState(
    () => new URLSearchParams(window.location.hash.slice(1)).get("token") || "",
  );
  const [grant, setGrant] = useState(null);
  const [ready, setReady] = useState(false);
  const [available, setAvailable] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [loadFailed, setLoadFailed] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [finished, setFinished] = useState(false);
  useEffect(() => {
    // Keep the one-use secret out of copied URLs and browser history.
    if (window.location.hash)
      window.history.replaceState(
        window.history.state,
        "",
        window.location.pathname,
      );
    let active = true;
    setLoadFailed(false);
    setError("");
    if (verify || token) {
      setReady(true);
      return;
    }
    Promise.all([api("/auth/recovery/status"), api("/auth/status")])
      .then(([session, status]) => {
        if (active) {
          setGrant(session.active ? session : null);
          setAvailable(Boolean(status.recovery_available));
          setReady(true);
        }
      })
      .catch((e) => {
        if (active) {
          setError(e.message);
          setLoadFailed(true);
          setReady(true);
        }
      });
    return () => {
      active = false;
    };
  }, [verify, token, attempt]);
  const perform = async (event) => {
    event.preventDefault();
    setError("");
    setMessage("");
    if (grant && password !== repeat) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      if (token) {
        await post(verify ? "/auth/email/verify" : "/auth/recovery/exchange", {
          token,
        });
        setToken("");
        if (verify) {
          setFinished(true);
          setMessage("Your recovery email is verified.");
        } else {
          const session = await api("/auth/recovery/status");
          if (!session.active)
            throw new Error(
              "This recovery session has expired. Request a new link.",
            );
          setGrant(session);
        }
      } else if (grant) {
        await post("/auth/recovery/password", { new_password: password });
        setPassword("");
        setRepeat("");
        setGrant(null);
        setFinished(true);
        setMessage(
          "Password changed. All sessions have been signed out. Sign in with your new password.",
        );
      } else {
        await post("/auth/recovery/request", { email });
        setMessage(
          "If this address is verified on an account, you’ll receive a recovery link. Check your inbox and spam folder.",
        );
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="auth-page" id="main">
      <form className="form-panel auth-card recovery-card" onSubmit={perform}>
        <Link className="auth-brand" to="/">
          Trackify<span>.</span>
        </Link>
        <h1>
          {verify
            ? "Verify recovery email"
            : grant
              ? "Change password"
              : "Recover your account"}
        </h1>
        <Notice error>{error}</Notice>
        <Notice>{message}</Notice>
        {!ready ? (
          <p role="status">Loading…</p>
        ) : loadFailed ? (
          <button
            type="button"
            className="button"
            onClick={() => {
              setReady(false);
              setAttempt((value) => value + 1);
            }}
          >
            Retry
          </button>
        ) : finished ? (
          <Link
            className="button primary"
            to={verify ? "/settings#account" : "/"}
          >
            {verify ? "Go to account" : "Sign in"}
          </Link>
        ) : token ? (
          <>
            <p className="muted">
              {verify
                ? "Confirm that you want to use this email for password recovery."
                : "Continue to set a new password. This link grants access only to password recovery."}
            </p>
            <button className="button primary" disabled={busy}>
              {busy ? "Please wait…" : "Continue"}
            </button>
          </>
        ) : grant ? (
          <>
            <p className="muted">Account: {grant.username}</p>
            <label className="field">
              New password
              <input
                type="password"
                autoComplete="new-password"
                minLength={10}
                maxLength={128}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <span className="hint">At least 10 characters.</span>
            </label>
            <label className="field">
              Repeat new password
              <input
                type="password"
                autoComplete="new-password"
                minLength={10}
                maxLength={128}
                required
                value={repeat}
                onChange={(e) => setRepeat(e.target.value)}
              />
            </label>
            <button className="button primary" disabled={busy}>
              {busy ? "Changing…" : "Change password"}
            </button>
          </>
        ) : verify ? (
          <p>
            This link is missing its verification token. Request another from
            Account settings.
          </p>
        ) : available ? (
          <>
            <p className="muted">
              Enter the recovery email you verified in Account settings.
            </p>
            <label className="field">
              Recovery email
              <input
                type="email"
                autoComplete="email"
                required
                maxLength={254}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
            <button className="button primary" disabled={busy}>
              {busy ? "Please wait…" : "Send recovery link"}
            </button>
          </>
        ) : (
          <p>
            Email recovery is not available.{" "}
            <Link to="/privacy#contact">Contact the operator</Link> for help.
          </p>
        )}
        {!finished && (
          <Link className="text-button" to="/">
            Back to sign in
          </Link>
        )}
      </form>
    </main>
  );
}
