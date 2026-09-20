import { vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import RecoveryPage from "../Auth/RecoveryPage";
import RecoveryEmail from "../Auth/RecoveryEmail";
import { api, post } from "../api";
vi.mock("../api", () => ({ api: vi.fn(), post: vi.fn() }));
beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/");
});
function page(props = {}) {
  return render(
    <MemoryRouter>
      <RecoveryPage {...props} />
    </MemoryRouter>,
  );
}

test("opening an email does not consume its token; continuation exchanges it without exposing library content", async () => {
  window.history.replaceState(
    null,
    "",
    "/account/recover#token=one-use-secret",
  );
  api.mockImplementation((path) =>
    Promise.resolve(
      path === "/auth/status"
        ? { recovery_available: true }
        : { active: true, username: "alice" },
    ),
  );
  post.mockResolvedValue({ ok: true });
  page();
  expect(window.location.hash).toBe("");
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", { name: "Continue" }));
  await screen.findByLabelText(/^New password/);
  expect(post).toHaveBeenCalledWith("/auth/recovery/exchange", {
    token: "one-use-secret",
  });
  fireEvent.change(screen.getByLabelText(/^New password/), {
    target: { value: "a-new-password" },
  });
  fireEvent.change(screen.getByLabelText("Repeat new password"), {
    target: { value: "does-not-match" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Change password" }));
  expect(
    await screen.findByText("The new passwords do not match."),
  ).toBeInTheDocument();
  expect(post).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText("Repeat new password"), {
    target: { value: "a-new-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Change password" }));
  await screen.findByRole("link", { name: "Sign in" });
  expect(post).toHaveBeenCalledWith("/auth/recovery/password", {
    new_password: "a-new-password",
  });
});

test("verification requires an explicit click and does not sign in", async () => {
  window.history.replaceState(
    null,
    "",
    "/account/verify-email#token=verification-secret",
  );
  post.mockResolvedValue({ ok: true });
  page({ verify: true });
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", { name: "Continue" }));
  await screen.findByText("Your recovery email is verified.");
  expect(post).toHaveBeenCalledWith("/auth/email/verify", {
    token: "verification-secret",
  });
  expect(api).not.toHaveBeenCalled();
});

test("disabled delivery does not offer a nonfunctional send button", async () => {
  api.mockResolvedValue({ active: false, recovery_available: false });
  page();
  await screen.findByText(/Email recovery is not available/);
  expect(
    screen.queryByRole("button", { name: "Send recovery link" }),
  ).not.toBeInTheDocument();
});

test("recovery request uses a neutral confirmation", async () => {
  api.mockImplementation((path) =>
    Promise.resolve(
      path === "/auth/status"
        ? { recovery_available: true }
        : { active: false },
    ),
  );
  post.mockResolvedValue({ ok: true });
  page();
  fireEvent.change(await screen.findByLabelText("Recovery email"), {
    target: { value: "alice@example.com" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send recovery link" }));
  await screen.findByText(/If this address is verified on an account/);
  expect(post).toHaveBeenCalledWith("/auth/recovery/request", {
    email: "alice@example.com",
  });
});

test("email controls require password and respect configured delivery", async () => {
  api.mockResolvedValue({
    available: false,
    email: "alice@example.com",
    pending_email: null,
  });
  render(<RecoveryEmail />);
  await screen.findByText("Verified: alice@example.com");
  expect(screen.getByRole("button", { name: "Verify email" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Remove email" })).toBeDisabled();
  fireEvent.change(
    screen.getByLabelText("Current password for recovery email"),
    { target: { value: "current-password" } },
  );
  api.mockResolvedValueOnce({ ok: true }).mockResolvedValueOnce({
    available: false,
    email: null,
    pending_email: null,
  });
  fireEvent.click(screen.getByRole("button", { name: "Remove email" }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith("/auth/email", {
      method: "DELETE",
      body: JSON.stringify({ current_password: "current-password" }),
    }),
  );
  await screen.findByText("Recovery email removed.");
});

test("a recovery status failure can be retried without claiming delivery is disabled", async () => {
  api.mockRejectedValueOnce(new Error("Temporarily offline"));
  api.mockResolvedValue({ active: false, recovery_available: true });
  page();
  await screen.findByText("Temporarily offline");
  expect(
    screen.queryByText(/Email recovery is not available/),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("button", { name: "Send recovery link" });
});
