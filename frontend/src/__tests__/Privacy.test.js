import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import AccountData from "../Auth/AccountData";
import PrivacyPage from "../Pages/PrivacyPage";
import { api, post } from "../api";
jest.mock("../api", () => ({ api: jest.fn(), post: jest.fn() }));
beforeEach(() => jest.resetAllMocks());

test("privacy notice is available without account context and shows the operator contact", async () => {
  api.mockResolvedValue({
    operator: "Trackify",
    contact: "privacy@example.com",
    hosting: "Test hosting",
    updated: "2026-09-14",
  });
  render(
    <MemoryRouter>
      <PrivacyPage />
    </MemoryRouter>,
  );
  expect(
    await screen.findByRole("link", { name: "privacy@example.com" }),
  ).toHaveAttribute("href", "mailto:privacy@example.com");
  expect(
    screen.getByRole("heading", { name: "Your controls and rights" }),
  ).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
});

test("permanent deletion requires the password and exact confirmation", async () => {
  const auth = { endSession: jest.fn() };
  post.mockResolvedValue({ ok: true, cleanup_pending: false });
  render(<AccountData auth={auth} />);
  fireEvent.click(screen.getByText("Delete account", { selector: "summary" }));
  const button = screen.getByRole("button", {
    name: "Permanently delete my account",
  });
  expect(button).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Password for account controls"), {
    target: { value: "test-only-password" },
  });
  fireEvent.change(screen.getByLabelText("Type DELETE to confirm"), {
    target: { value: "delete" },
  });
  expect(button).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Type DELETE to confirm"), {
    target: { value: "DELETE" },
  });
  fireEvent.click(button);
  await waitFor(() => expect(auth.endSession).toHaveBeenCalled());
  expect(post).toHaveBeenCalledWith("/auth/delete", {
    current_password: "test-only-password",
    confirmation: "DELETE",
  });
});

test("failed deletion leaves the account visible and pending cleanup is explained", async () => {
  const auth = { endSession: jest.fn() };
  post.mockRejectedValueOnce(new Error("Storage unavailable"));
  render(<AccountData auth={auth} />);
  fireEvent.click(screen.getByText("Delete account", { selector: "summary" }));
  fireEvent.change(screen.getByLabelText("Password for account controls"), {
    target: { value: "test-only-password" },
  });
  fireEvent.change(screen.getByLabelText("Type DELETE to confirm"), {
    target: { value: "DELETE" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Permanently delete my account" }),
  );
  expect(await screen.findByText("Storage unavailable")).toBeInTheDocument();
  expect(auth.endSession).not.toHaveBeenCalled();
  post.mockResolvedValue({ ok: true, cleanup_pending: true });
  fireEvent.click(
    screen.getByRole("button", { name: "Permanently delete my account" }),
  );
  await waitFor(() =>
    expect(auth.endSession).toHaveBeenCalledWith(
      expect.stringContaining("queued"),
    ),
  );
});

test("export failures do not trigger a download or end the session", async () => {
  const auth = { endSession: jest.fn() };
  post.mockRejectedValue(new Error("Could not download data"));
  render(<AccountData auth={auth} />);
  fireEvent.change(screen.getByLabelText("Password for account controls"), {
    target: { value: "test-only-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Download my data" }));
  expect(
    await screen.findByText("Could not download data"),
  ).toBeInTheDocument();
  expect(auth.endSession).not.toHaveBeenCalled();
  expect(post).toHaveBeenCalledWith("/auth/export", {
    current_password: "test-only-password",
  });
});
