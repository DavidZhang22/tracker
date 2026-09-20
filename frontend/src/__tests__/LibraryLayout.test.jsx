import { vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import LibraryPage from "../Pages/LibraryPage";
import Footer from "../Components/Footer";
import { api } from "../api";

vi.mock("../api", () => ({
  api: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  refreshLibrary: vi.fn(),
  examples: [],
  checked: () => "Today",
}));

const item = {
  id: "one",
  title: "A series",
  url: "https://example.com/series",
  kind: "novel",
  created_at: "2026-09-19",
  favorite: false,
  ignored: false,
  read_count: 5,
  unread_count: 3,
  new_count: 1,
  total_count: 8,
  ignored_count: 0,
};

beforeEach(() => vi.clearAllMocks());

test("compact totals retain link counts separately from item filter counts", async () => {
  api.mockResolvedValue([
    item,
    {
      ...item,
      id: "two",
      title: "A muted favorite",
      ignored: true,
      favorite: true,
    },
  ]);
  render(
    <MemoryRouter>
      <LibraryPage />
    </MemoryRouter>,
  );
  await screen.findByText("A series");
  const totals = screen.getByLabelText("Library link totals");
  expect(
    within(totals).getByRole("button", { name: "3 unread links" }),
  ).toBeInTheDocument();
  expect(
    within(totals).getByRole("button", { name: "1 new link" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /^Unread\s*1$/ }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /^Favorites\s*1$/ }),
  ).toBeInTheDocument();
  fireEvent.click(
    within(totals).getByRole("button", { name: "3 unread links" }),
  );
  expect(screen.getAllByRole("article")).toHaveLength(1);
  expect(screen.queryByText("A muted favorite")).not.toBeInTheDocument();
});

test("consolidated row retains progress, last check, mute state, and actions", async () => {
  api.mockResolvedValue([{ ...item, ignored: true }]);
  render(
    <MemoryRouter>
      <LibraryPage />
    </MemoryRouter>,
  );
  const row = (await screen.findByText("A series")).closest("article");
  expect(within(row).getByText("Muted")).toBeInTheDocument();
  expect(within(row).getByText("5 of 8 read")).toBeInTheDocument();
  expect(within(row).getByText("Checked Today")).toBeInTheDocument();
  expect(
    within(row).getByRole("progressbar", {
      name: "Reading progress for A series",
    }),
  ).toHaveAttribute("value", "5");
  expect(
    within(row).getByRole("button", { name: "Favorite A series" }),
  ).toBeEnabled();
  fireEvent.click(
    within(row).getByRole("button", { name: "Actions for A series" }),
  );
  expect(
    await screen.findByRole("menuitem", { name: "Unmute" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "Delete" })).toBeInTheDocument();
});

test("footer keeps privacy, terms, contact and the library route available", () => {
  render(
    <MemoryRouter>
      <Footer />
    </MemoryRouter>,
  );
  const footer = screen.getByRole("contentinfo");
  expect(
    within(footer).getByRole("link", { name: "Trackify" }),
  ).toHaveAttribute("href", "/");
  expect(within(footer).getByRole("link", { name: "Privacy" })).toHaveAttribute(
    "href",
    "/privacy",
  );
  expect(within(footer).getByRole("link", { name: "Terms" })).toHaveAttribute(
    "href",
    "/terms",
  );
  expect(within(footer).getByRole("link", { name: "Contact" })).toHaveAttribute(
    "href",
    "/privacy#contact",
  );
});

test("Library shows its count and limit in the heading while Trash keeps its own count", async () => {
  api.mockImplementation(async (path) =>
    path === "/items?trash=true"
      ? [{ ...item, id: "deleted", title: "Deleted series", deleted: true }]
      : [item],
  );
  render(
    <MemoryRouter>
      <LibraryPage />
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("heading", { name: "Library", exact: true }),
  ).toBeInTheDocument();
  await screen.findByText("A series");
  expect(
    screen.getByRole("heading", { name: "Library 1/500", exact: true }),
  ).toBeInTheDocument();
  expect(screen.getAllByText("1/500")).toHaveLength(1);
  expect(
    screen.queryByText("Up to 500 items per account, including Trash."),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("Saved views")).not.toBeInTheDocument();
  expect(api).not.toHaveBeenCalledWith("/views");
  fireEvent.click(screen.getByRole("button", { name: "Trash", exact: true }));
  await screen.findByText("Deleted series");
  expect(
    screen.getByRole("heading", { name: "Trash 1", exact: true }),
  ).toBeInTheDocument();
  expect(screen.queryByText("1/500")).not.toBeInTheDocument();
});
