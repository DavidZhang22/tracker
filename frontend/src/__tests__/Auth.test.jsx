import { vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { AuthBoundary, useAuth } from "../Auth/Auth";
import { MemoryRouter } from "react-router-dom";
import { api, post } from "../api";
vi.mock("../api", () => ({ api: vi.fn(), post: vi.fn() }));
const peers = new Set();
class TestBroadcastChannel {
  constructor(name) {
    this.name = name;
    peers.add(this);
  }
  postMessage(data) {
    for (const peer of peers)
      if (peer !== this && peer.name === this.name) peer.onmessage?.({ data });
  }
  close() {
    peers.delete(this);
  }
}
beforeEach(() => {
  vi.resetAllMocks();
  peers.clear();
  vi.stubGlobal("BroadcastChannel", TestBroadcastChannel);
});
afterEach(() => vi.unstubAllGlobals());
function changedSession() {
  const peer = new BroadcastChannel("trackify:session-change");
  peer.postMessage({ type: "changed", sender: "another-tab" });
  peer.close();
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function PrivateContent() {
  const auth = useAuth();
  return (
    <div>
      Private library
      {auth.user && (
        <>
          <span>{auth.user.username}</span>
          <button onClick={auth.logout}>Sign out</button>
        </>
      )}
    </div>
  );
}
function view() {
  render(
    <MemoryRouter>
      <AuthBoundary>
        <PrivateContent />
      </AuthBoundary>
    </MemoryRouter>,
  );
}

test("signed-out users see sign-in before any library content", async () => {
  api.mockResolvedValue({ required: true, registration: false, user: null });
  post.mockResolvedValue({ user: { username: "alice" } });
  view();
  await screen.findByRole("heading", { name: "Sign in" });
  expect(screen.queryByText("Private library")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Username"), {
    target: { value: "alice" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "not-a-real-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText("alice")).toBeInTheDocument();
  expect(post).toHaveBeenCalledWith("/auth/login", {
    username: "alice",
    password: "not-a-real-password",
  });
  await act(async () =>
    fireEvent.click(screen.getByRole("button", { name: "Sign out" })),
  );
  expect(screen.queryByText("Private library")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
});

test("invite-only signup sends invite and password without storing them in the browser", async () => {
  api.mockResolvedValue({ required: true, registration: true, user: null });
  post.mockResolvedValue({ user: { username: "alice" } });
  view();
  fireEvent.click(
    await screen.findByRole("button", {
      name: /Create an account with an invite/,
    }),
  );
  fireEvent.change(screen.getByLabelText("Username"), {
    target: { value: "alice" },
  });
  fireEvent.change(screen.getByLabelText(/Password/), {
    target: { value: "not-a-real-password" },
  });
  fireEvent.change(screen.getByLabelText(/Invite code/), {
    target: { value: "not-a-real-invitation" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  await screen.findByText("alice");
  expect(post).toHaveBeenCalledWith("/auth/register", {
    username: "alice",
    password: "not-a-real-password",
    invite_code: "not-a-real-invitation",
  });
  expect(localStorage.length).toBe(0);
});

test("expired sessions remove the private view", async () => {
  api.mockResolvedValueOnce({
    required: true,
    user: { username: "alice" },
    registration: false,
  });
  view();
  await screen.findByText("alice");
  api.mockResolvedValue({ required: true, user: null, registration: false });
  await act(async () =>
    window.dispatchEvent(new Event("trackify:unauthorized")),
  );
  await waitFor(() =>
    expect(screen.queryByText("Private library")).not.toBeInTheDocument(),
  );
  expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
});

test("public signup requires no invite and signs in to a private library", async () => {
  api.mockResolvedValue({
    required: true,
    registration: true,
    invite_required: false,
    user: null,
  });
  post.mockResolvedValue({ user: { username: "alice" } });
  view();
  fireEvent.click(
    await screen.findByRole("button", { name: "Create an account" }),
  );
  expect(screen.queryByLabelText(/Invite code/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Username"), {
    target: { value: "alice" },
  });
  fireEvent.change(screen.getByLabelText(/Password/), {
    target: { value: "not-a-real-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  await screen.findByText("Private library");
  expect(post).toHaveBeenCalledWith("/auth/register", {
    username: "alice",
    password: "not-a-real-password",
  });
  expect(localStorage.length).toBe(0);
});

test("public signup limits leave the form available to retry", async () => {
  api.mockResolvedValue({
    required: true,
    registration: true,
    invite_required: false,
    user: null,
  });
  post.mockRejectedValue(
    new Error(
      "Account creation is temporarily limited. Please try again later.",
    ),
  );
  view();
  fireEvent.click(
    await screen.findByRole("button", { name: "Create an account" }),
  );
  fireEvent.change(screen.getByLabelText("Username"), {
    target: { value: "alice" },
  });
  fireEvent.change(screen.getByLabelText(/Password/), {
    target: { value: "not-a-real-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  await screen.findByText(/Account creation is temporarily limited/);
  expect(screen.getByRole("button", { name: "Create account" })).toBeEnabled();
  expect(screen.queryByText("Private library")).not.toBeInTheDocument();
});

test("local mode still opens the existing personal library", async () => {
  api.mockResolvedValue({ required: false, user: null, registration: false });
  view();
  expect(await screen.findByText("Private library")).toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Sign in" }),
  ).not.toBeInTheDocument();
});

test("a revoked session hides private content before a failing recheck completes", async () => {
  api.mockResolvedValueOnce({
    required: true,
    user: { id: "alice", username: "alice" },
  });
  view();
  await screen.findByText("alice");
  const check = deferred();
  api.mockReturnValueOnce(check.promise);
  act(() => window.dispatchEvent(new Event("trackify:unauthorized")));
  expect(screen.queryByText("Private library")).not.toBeInTheDocument();
  await act(async () => check.reject(new Error("Temporarily offline")));
  expect(screen.getByText("Temporarily offline")).toBeInTheDocument();
  expect(screen.queryByText("Private library")).not.toBeInTheDocument();
});

test("an account change in another tab clears private state and ignores older checks", async () => {
  api.mockResolvedValueOnce({
    required: true,
    user: { id: "alice", username: "alice" },
  });
  view();
  await screen.findByText("alice");
  const stale = deferred(),
    current = deferred();
  api.mockReturnValueOnce(stale.promise).mockReturnValueOnce(current.promise);
  act(changedSession);
  expect(screen.queryByText("Private library")).not.toBeInTheDocument();
  const previousSignal = api.mock.calls[1][1].signal;
  act(changedSession);
  expect(previousSignal.aborted).toBe(true);
  await act(async () =>
    current.resolve({ required: true, user: { id: "bob", username: "bob" } }),
  );
  expect(screen.getByText("bob")).toBeInTheDocument();
  await act(async () =>
    stale.resolve({ required: true, user: { id: "alice", username: "alice" } }),
  );
  expect(screen.queryByText("alice")).not.toBeInTheDocument();
  expect(screen.getByText("bob")).toBeInTheDocument();
});

test("the storage fallback hides private views without persisting account data", async () => {
  vi.stubGlobal("BroadcastChannel", undefined);
  api.mockResolvedValueOnce({ required: true, user: { username: "alice" } });
  view();
  await screen.findByText("alice");
  api.mockResolvedValueOnce({
    required: true,
    registration: false,
    user: null,
  });
  await act(async () =>
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "trackify:session-change",
        newValue: "change-marker",
      }),
    ),
  );
  expect(screen.queryByText("Private library")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  expect(localStorage.length).toBe(0);
});

test("pages restored from browser history revalidate before showing private content", async () => {
  api.mockResolvedValueOnce({ required: true, user: { username: "alice" } });
  view();
  await screen.findByText("alice");
  const check = deferred();
  api.mockReturnValueOnce(check.promise);
  const event = new Event("pageshow");
  Object.defineProperty(event, "persisted", { value: true });
  act(() => window.dispatchEvent(event));
  expect(screen.queryByText("Private library")).not.toBeInTheDocument();
  await act(async () => check.resolve({ required: true, user: null }));
  expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
});

test("signin and signout tell other tabs to recheck without broadcasting user data", async () => {
  const peer = new BroadcastChannel("trackify:session-change");
  peer.onmessage = vi.fn();
  api.mockResolvedValueOnce({
    required: true,
    registration: false,
    user: null,
  });
  post.mockResolvedValue({ user: { id: "alice-id", username: "alice" } });
  view();
  await screen.findByRole("heading", { name: "Sign in" });
  fireEvent.change(screen.getByLabelText("Username"), {
    target: { value: "alice" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "not-a-real-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await screen.findByText("alice");
  expect(peer.onmessage).toHaveBeenCalledTimes(1);
  expect(peer.onmessage.mock.calls[0][0].data).toEqual({
    type: "changed",
    sender: expect.any(String),
  });
  expect(api).toHaveBeenCalledTimes(1);
  await act(async () =>
    fireEvent.click(screen.getByRole("button", { name: "Sign out" })),
  );
  expect(peer.onmessage).toHaveBeenCalledTimes(2);
  expect(screen.queryByText("Private library")).not.toBeInTheDocument();
  expect(localStorage.length).toBe(0);
  peer.close();
});
