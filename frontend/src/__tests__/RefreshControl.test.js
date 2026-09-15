import { fireEvent, render, screen } from "@testing-library/react";
import { RefreshControl } from "../Components/RowTools";

test("refresh defaults to lightweight and exposes a separate deep action", async () => {
  const refresh = jest.fn();
  render(<RefreshControl label="Refresh item" onRefresh={refresh} />);
  fireEvent.click(screen.getByRole("button", { name: "Refresh item" }));
  expect(refresh).toHaveBeenLastCalledWith(false);
  fireEvent.click(
    screen.getByRole("button", { name: "Options for refresh item" }),
  );
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Deep refresh" }),
  );
  expect(refresh).toHaveBeenLastCalledWith(true);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});

test("both library refresh controls are disabled while working", () => {
  render(<RefreshControl label="Refresh all" onRefresh={jest.fn()} busy />);
  expect(screen.getByRole("button", { name: "Refreshing…" })).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Options for refresh all" }),
  ).toBeDisabled();
});

test("library menu names the full-library operation", async () => {
  const refresh = jest.fn();
  render(<RefreshControl label="Refresh all" onRefresh={refresh} />);
  fireEvent.click(
    screen.getByRole("button", { name: "Options for refresh all" }),
  );
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Deep refresh all" }),
  );
  expect(refresh).toHaveBeenCalledWith(true);
});
