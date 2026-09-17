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
jest.mock("../api", () => ({ api: jest.fn(), post: jest.fn() }));
beforeEach(() => jest.resetAllMocks());

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
