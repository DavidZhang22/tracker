import { vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RefreshControl } from "../Components/RowTools";

test("refresh defaults to lightweight and exposes a separate deep action", async () => {
  const refresh = vi.fn();
  render(<RefreshControl label="Refresh item" onRefresh={refresh} />);
  fireEvent.click(screen.getByRole("button", { name: "Refresh item" }));
  expect(refresh).toHaveBeenLastCalledWith(false);
  fireEvent.click(
    screen.getByRole("button", { name: "Options for refresh item" }),
  );
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Full Refresh" }),
  );
  expect(refresh).toHaveBeenLastCalledWith(true);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});

test("both library refresh controls are disabled while working", () => {
  render(<RefreshControl label="Refresh" onRefresh={vi.fn()} busy />);
  expect(screen.getByRole("button", { name: "Refreshing…" })).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Options for refresh" }),
  ).toBeDisabled();
});

test("library menu names the full-library operation", async () => {
  const refresh = vi.fn();
  render(<RefreshControl label="Refresh" onRefresh={refresh} />);
  fireEvent.click(screen.getByRole("button", { name: "Options for refresh" }));
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Full Refresh" }),
  );
  expect(refresh).toHaveBeenCalledWith(true);
});
