import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link } from "react-router-dom";
import { api, post } from "../api";
import { Notice } from "../Components/Notice";
import AppLoading from "../Components/AppLoading";
import PublicLayout from "../Components/PublicLayout";
import { notifySessionChange, watchSessionChanges } from "./sessionEvents";

const AuthContext = createContext({ required: false, user: null });
export const useAuth = () => useContext(AuthContext);

export function AuthBoundary({ children }) {
  const [sessionNotice, setSessionNotice] = useState("");
  const [status, setStatus] = useState(null),
    [error, setError] = useState("");
  const pending = useRef(null);
  const generation = useRef(0);
  const replaceStatus = useCallback((next) => {
    generation.current += 1;
    pending.current?.abort();
    setStatus(next);
  }, []);
  const load = useCallback(async () => {
    const current = ++generation.current;
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setStatus(null);
    setError("");
    try {
      const next = await api("/auth/status", { signal: controller.signal });
      if (current === generation.current) setStatus(next);
    } catch (e) {
      if (current === generation.current && e.name !== "AbortError")
        setError(e.message);
    }
  }, []);
  useEffect(() => {
    load();
    const stopWatching = watchSessionChanges(load);
    const restored = (event) => {
      if (event.persisted) load();
    };
    window.addEventListener("trackify:unauthorized", load);
    window.addEventListener("pageshow", restored);
    return () => {
      generation.current += 1;
      pending.current?.abort();
      stopWatching();
      window.removeEventListener("trackify:unauthorized", load);
      window.removeEventListener("pageshow", restored);
    };
  }, [load]);
  const endSession = (message = "") => {
    setSessionNotice(message);
    replaceStatus({ ...status, user: null });
    notifySessionChange();
  };
  if (!status)
    return (
      <AppLoading>
        {error && (
          <>
            <Notice error>{error}</Notice>
            <button className="button" onClick={load}>
              Retry
            </button>
          </>
        )}
      </AppLoading>
    );
  if (status.required && !status.user)
    return (
      <PublicLayout compact>
        <SignIn
          notice={sessionNotice}
          registration={status.registration}
          inviteRequired={status.invite_required ?? true}
          onSignedIn={(user) => {
            replaceStatus({ ...status, user });
            notifySessionChange();
          }}
        />
      </PublicLayout>
    );
  return (
    <AuthContext.Provider
      key={status.user?.id ?? status.user?.username ?? "local"}
      value={{
        ...status,
        endSession,
        logout: async () => {
          await post("/auth/logout");
          endSession();
        },
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

function SignIn({ registration, inviteRequired, onSignedIn, notice }) {
  const [create, setCreate] = useState(false),
    [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [invite, setInvite] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await post(create ? "/auth/register" : "/auth/login", {
        username,
        password,
        ...(create && inviteRequired ? { invite_code: invite } : {}),
      });
      setPassword("");
      setInvite("");
      onSignedIn(result.user);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main id="main" tabIndex={-1} className="auth-page">
      <form className="form-panel auth-card" onSubmit={submit}>
        <div className="auth-brand">
          Trackify<span>.</span>
        </div>
        <h1>{create ? "Create account" : "Sign in"}</h1>
        <p className="muted">
          Media and reading progress, saved to your account.
        </p>
        <Notice error>{error}</Notice>
        <Notice>{notice}</Notice>
        <label className="field">
          Username
          <input
            required
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            minLength={3}
            maxLength={40}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label className="field">
          Password
          <input
            type="password"
            required
            autoComplete={create ? "new-password" : "current-password"}
            minLength={create ? 10 : 1}
            maxLength={128}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {create && <span className="hint">At least 10 characters.</span>}
        </label>
        {create && inviteRequired && (
          <label className="field">
            Invite code
            <input
              type="password"
              required
              autoComplete="off"
              value={invite}
              onChange={(e) => setInvite(e.target.value)}
            />
            <span className="hint">Ask the site owner for an invite code.</span>
          </label>
        )}
        <button className="button primary" disabled={busy}>
          {busy ? "Please wait…" : create ? "Create account" : "Sign in"}
        </button>
        {registration && (
          <button
            type="button"
            className="text-button auth-switch"
            disabled={busy}
            onClick={() => {
              setCreate(!create);
              setError("");
              setPassword("");
            }}
          >
            {create
              ? "Already have an account? Sign in"
              : inviteRequired
                ? "Create an account with an invite"
                : "Create an account"}
          </button>
        )}
        {!create && (
          <p className="hint">
            <Link to="/account/recover">Forgot your password?</Link>
          </p>
        )}
        {create && (
          <p className="hint">
            By creating an account, you agree to the{" "}
            <Link to="/terms">terms of use</Link>.
          </p>
        )}
      </form>
    </main>
  );
}
