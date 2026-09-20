import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeftIcon } from "@heroicons/react/outline";
import { Icon } from "../Components/Icons";
import { post } from "../api";
import { Notice } from "../Components/Notice";
import { useAuth } from "./Auth";
import AccountData from "./AccountData";
import RecoveryEmail from "./RecoveryEmail";

export default function AccountPage({ embedded = false }) {
  const auth = useAuth();
  const [current, setCurrent] = useState(""),
    [password, setPassword] = useState(""),
    [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const submit = async (event) => {
    event.preventDefault();
    setMessage("");
    setError("");
    if (password !== repeat) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await post("/auth/password", {
        current_password: current,
        new_password: password,
      });
      setCurrent("");
      setPassword("");
      setRepeat("");
      setMessage("Password changed. Other sessions have been signed out.");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {!embedded && (
        <Link className="back-link" to="/">
          <Icon as={ArrowLeftIcon} />
          Library
        </Link>
      )}
      {embedded ? <h2>Account</h2> : <h1>Account</h1>}
      {auth.required ? (
        <>
          <form className="form-panel account-card" onSubmit={submit}>
            <p className="account-identity">{auth.user.username}</p>
            <h3>Change password</h3>
            <Notice error>{error}</Notice>
            <Notice>{message}</Notice>
            <label className="field">
              Current password
              <input
                type="password"
                autoComplete="current-password"
                required
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
              />
            </label>
            <label className="field">
              New password
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={10}
                maxLength={128}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            <label className="field">
              Repeat new password
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={10}
                maxLength={128}
                value={repeat}
                onChange={(e) => setRepeat(e.target.value)}
              />
            </label>
            <button className="button primary" disabled={busy}>
              Change password
            </button>
          </form>
          <RecoveryEmail />
          <AccountData auth={auth} />
        </>
      ) : (
        <p>This local server uses a personal library without sign-in.</p>
      )}
    </>
  );
}
