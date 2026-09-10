import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthBoundary } from "./Auth";
import { Shell } from "./Tracker";
import { SelectionBar, useSelection } from "./RowTools";
import { api } from "./api";

jest.mock("./api", () => ({
  api: jest.fn(),
  post: jest.fn(),
  patch: jest.fn(),
  examples: [],
}));

beforeEach(() => jest.resetAllMocks());

test("navigation closes after choosing Account and Escape returns focus to Menu", async () => {
  api.mockImplementation((path) =>
    Promise.resolve(
      path === "/auth/status"
        ? { required: true, user: { username: "reader" }, registration: false }
        : [],
    ),
  );
  render(
    <MemoryRouter>
      <AuthBoundary>
        <Shell />
      </AuthBoundary>
    </MemoryRouter>,
  );
  const menu = await screen.findByRole("button", { name: "Menu", exact: true });
  await screen.findByRole("heading", { name: "Start your collection" });
  expect(menu).toHaveAttribute("aria-expanded", "false");
  expect(
    screen
      .getByRole("link", { name: "reader · Account" })
      .closest(".sidebar-foot")
      .querySelector(".small-dot"),
  ).toBeNull();
  await act(async () => fireEvent.click(menu));
  expect(menu).toHaveAttribute("aria-expanded", "true");
  await act(async () =>
    fireEvent.click(screen.getByRole("link", { name: "reader · Account" })),
  );
  expect(
    await screen.findByRole("heading", { name: "Account" }),
  ).toBeInTheDocument();
  expect(menu).toHaveAttribute("aria-expanded", "false");
  await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
  await act(async () => fireEvent.click(menu));
  screen.getByRole("link", { name: "reader · Account" }).focus();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(menu).toHaveAttribute("aria-expanded", "false");
  expect(menu).toHaveFocus();
});

function SelectionExample({ scope, onAction }) {
  const selection = useSelection(scope);
  return (
    <>
      <SelectionBar
        selection={selection}
        visible={["one", "two"]}
        onAction={onAction}
        links
      />
      {selection.selecting && (
        <label>
          <input
            type="checkbox"
            checked={selection.ids.includes("one")}
            onChange={() => selection.toggle("one")}
          />
          Select first chapter
        </label>
      )}
    </>
  );
}

test("selection can be entered and cancelled before or after choosing links", () => {
  const onAction = jest.fn();
  render(<SelectionExample scope="all" onAction={onAction} />);
  expect(
    screen.queryByLabelText("Select first chapter"),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Select links", exact: true }),
  );
  expect(screen.getByLabelText("Select first chapter")).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Cancel selection" }));
  expect(
    screen.queryByLabelText("Select first chapter"),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Select links", exact: true }),
  );
  fireEvent.click(screen.getByLabelText("Select first chapter"));
  fireEvent.click(screen.getByRole("button", { name: "Cancel selection" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Select links", exact: true }),
  );
  expect(screen.getByLabelText("Select first chapter")).not.toBeChecked();
  expect(onAction).not.toHaveBeenCalled();
});

test("moving to a different list exits selection mode and discards old IDs", () => {
  const onAction = jest.fn();
  const { rerender } = render(
    <SelectionExample scope="all" onAction={onAction} />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Select links", exact: true }),
  );
  fireEvent.click(screen.getByLabelText("Select first chapter"));
  rerender(<SelectionExample scope="unread" onAction={onAction} />);
  expect(
    screen.queryByRole("button", { name: "Cancel selection" }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Select links", exact: true }),
  );
  expect(screen.getByLabelText("Select first chapter")).not.toBeChecked();
  expect(onAction).not.toHaveBeenCalled();
});
