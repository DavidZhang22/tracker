import { vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import ItemPage from "../Pages/ItemPage";
import { api, post, patch } from "../api";

vi.mock("../api", () => ({
  api: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  checked: () => "Today",
  day: () => "Today",
  examples: [],
}));
const item = {
  id: "one",
  title: "Series",
  url: "https://example.org",
  kind: "novel",
  total_count: 60,
  unread_count: 60,
  read_count: 0,
  new_count: 0,
  ignored_count: 0,
  methods: [],
  auto_read: true,
};
const rows = [1, 2, 3].map((n) => ({
  id: `c${n}`,
  title: `Chapter ${n}`,
  url: `https://example.org/${n}`,
  number: n,
  read: false,
}));
const result = { links: rows, total: 60, sort_used: "number" };
function view() {
  render(
    <MemoryRouter initialEntries={["/items/one"]}>
      <Routes>
        <Route path="/items/:id" element={<ItemPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  api.mockImplementation((path) =>
    Promise.resolve(path.includes("/links?") ? result : item),
  );
  post.mockResolvedValue({ updated: 2 });
  patch.mockResolvedValue({ ok: true });
});
const click = async (...args) => act(async () => fireEvent.click(...args));
const requests = () =>
  api.mock.calls.filter(([path]) => path.includes("/links?"));

test("search debounces typing, retains empty results without flashing, and reuses cached searches", async () => {
  const pending = new Map();
  api.mockImplementation((path) => {
    if (!path.includes("/links?")) return Promise.resolve(item);
    const search = new URLSearchParams(path.split("?")[1]).get("search");
    return search
      ? new Promise((resolve) => pending.set(search, resolve))
      : Promise.resolve({ ...result, links: [], total: 0 });
  });
  view();
  const empty = await screen.findByRole("heading", {
    name: "No links in this view",
  });
  fireEvent.change(screen.getByLabelText("Search links"), {
    target: { value: "a" },
  });
  fireEvent.change(screen.getByLabelText("Search links"), {
    target: { value: "ab" },
  });
  expect(requests()).toHaveLength(1);
  expect(screen.queryByText("Loading links…")).not.toBeInTheDocument();
  await waitFor(() => expect(pending.has("ab")).toBe(true));
  expect(pending.has("a")).toBe(false);
  await act(async () => pending.get("ab")({ ...result, links: [], total: 0 }));
  expect(screen.getByRole("heading", { name: "No links in this view" })).toBe(
    empty,
  );
  fireEvent.change(screen.getByLabelText("Search links"), {
    target: { value: "" },
  });
  fireEvent.submit(screen.getByRole("search", { name: "Link search" }));
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "No links in this view" })).toBe(
      empty,
    ),
  );
  expect(requests()).toHaveLength(2);
});

test("Search submits immediately and an obsolete response cannot replace newer results", async () => {
  const pending = new Map();
  api.mockImplementation((path) => {
    const search =
      path.includes("?") &&
      new URLSearchParams(path.split("?")[1]).get("search");
    return search
      ? new Promise((resolve) => pending.set(search, resolve))
      : Promise.resolve(path.includes("/links?") ? result : item);
  });
  view();
  await screen.findByText("Chapter 1");
  fireEvent.change(screen.getByLabelText("Search links"), {
    target: { value: "old" },
  });
  await act(async () =>
    fireEvent.submit(screen.getByRole("search", { name: "Link search" })),
  );
  await waitFor(() => expect(pending.has("old")).toBe(true));
  fireEvent.change(screen.getByLabelText("Search links"), {
    target: { value: "new" },
  });
  fireEvent.submit(screen.getByRole("search", { name: "Link search" }));
  await waitFor(() => expect(pending.has("new")).toBe(true));
  await act(async () =>
    pending.get("new")({ ...result, links: [rows[2]], total: 1 }),
  );
  await act(async () =>
    pending.get("old")({ ...result, links: [rows[0]], total: 1 }),
  );
  expect(screen.getByText("Chapter 3")).toBeInTheDocument();
  expect(screen.queryByText("Chapter 1")).not.toBeInTheDocument();
});

test("pattern selection combines every-other with a range and keeps off-page IDs", async () => {
  post.mockResolvedValue({ ids: ["c2", "c52"], total: 60 });
  view();
  await screen.findByText("Chapter 1");
  fireEvent.change(screen.getByLabelText("Selection options"), {
    target: { value: "pattern" },
  });
  fireEvent.change(screen.getByLabelText("Starting with position"), {
    target: { value: "2" },
  });
  fireEvent.change(screen.getByLabelText("Through position"), {
    target: { value: "55" },
  });
  await click(screen.getByRole("button", { name: "Apply selection" }));
  await screen.findByText("2 selected");
  expect(post).toHaveBeenCalledWith("/items/one/link-selection", {
    filter: "all",
    search: "",
    sort: "auto",
    direction: "desc",
    pattern: { every: 2, starting: 2, first: 1, last: 55 },
  });
  await click(screen.getByRole("button", { name: "Favorite", exact: true }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/links/bulk", {
      action: "favorite",
      ids: ["c2", "c52"],
      item_id: "one",
    }),
  );
});

test("shift selection and merge send the selected rows in the current view", async () => {
  view();
  await screen.findByText("Chapter 1");
  await click(screen.getByLabelText("Select link: Chapter 1"));
  await click(screen.getByLabelText("Select link: Chapter 3"), {
    shiftKey: true,
  });
  expect(screen.getByText("3 selected")).toBeInTheDocument();
  await act(async () =>
    fireEvent.change(screen.getByLabelText("More bulk actions"), {
      target: { value: "merge" },
    }),
  );
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/items/one/link-groups", {
      action: "merge",
      ids: ["c1", "c2", "c3"],
      filter: "all",
      search: "",
      sort: "auto",
      direction: "desc",
    }),
  );
});

test("canceling selection resets the anchor for a later Shift-click", async () => {
  view();
  await screen.findByText("Chapter 1");
  await click(screen.getByLabelText("Select link: Chapter 1"));
  await click(screen.getByRole("button", { name: "Cancel selection" }));
  await click(screen.getByLabelText("Select link: Chapter 3"), {
    shiftKey: true,
  });
  expect(screen.getByText("1 selected")).toBeInTheDocument();
  expect(screen.getByLabelText("Select link: Chapter 1")).not.toBeChecked();
});

test("changing items isolates the old item's pending updates and search state", async () => {
  let finishUpdate, finishLoad;
  patch.mockImplementation(
    () =>
      new Promise((resolve) => {
        finishUpdate = resolve;
      }),
  );
  api.mockImplementation((path) => {
    if (path === "/items/two")
      return new Promise((resolve) => {
        finishLoad = resolve;
      });
    return Promise.resolve(path.includes("/links?") ? result : item);
  });
  render(
    <MemoryRouter initialEntries={["/items/one"]}>
      <Link to="/items/two?search=second">Open second item</Link>
      <Routes>
        <Route path="/items/:id" element={<ItemPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByText("Chapter 1");
  await click(screen.getByLabelText("Favorite item"));
  await click(screen.getByRole("link", { name: "Open second item" }));
  expect(
    screen.queryByRole("heading", { name: "Series" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Favorite item")).not.toBeInTheDocument();
  await act(async () =>
    finishLoad({ ...item, id: "two", title: "Second series" }),
  );
  expect(screen.getByLabelText("Search links")).toHaveValue("second");
  await act(async () => finishUpdate({ ...item, favorite: true }));
  expect(
    screen.getByRole("heading", { name: "Second series" }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Favorite item")).toBeInTheDocument();
});

test("a merged row exposes every URL, supports separation and uses Muted wording", async () => {
  api.mockImplementation((path) =>
    Promise.resolve(
      path.includes("/links?")
        ? { ...result, links: [{ ...rows[0], link_count: 3, members: rows }] }
        : item,
    ),
  );
  view();
  await screen.findByText("3 links in this entry");
  await click(screen.getByText("3 links in this entry"));
  expect(screen.getByRole("link", { name: "Chapter 2" })).toHaveAttribute(
    "href",
    rows[1].url,
  );
  expect(
    screen.getByRole("button", { name: "Muted", exact: true }),
  ).toBeInTheDocument();
  await click(screen.getByLabelText("Actions for Chapter 1"));
  await click(await screen.findByRole("menuitem", { name: "Separate links" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/items/one/link-groups",
      expect.objectContaining({ action: "separate", ids: ["c1"] }),
    ),
  );
});

test("cached views are invalidated after a link changes", async () => {
  view();
  await screen.findByText("Chapter 1");
  await click(screen.getByRole("button", { name: "Favorites", exact: true }));
  await waitFor(() => expect(requests()).toHaveLength(2));
  await click(screen.getByRole("button", { name: "All links", exact: true }));
  await waitFor(() =>
    expect(screen.getByLabelText("Select this page")).toBeEnabled(),
  );
  expect(requests()).toHaveLength(2);
  await click(screen.getByLabelText("Favorite link: Chapter 1"));
  await waitFor(() => expect(requests()).toHaveLength(3));
});

test("Submitting the search field retries a failed request without requiring a different query", async () => {
  let fail = true;
  api.mockImplementation((path) => {
    if (!path.includes("/links?")) return Promise.resolve(item);
    if (fail) return Promise.reject(new Error("Storage unavailable"));
    return Promise.resolve(result);
  });
  view();
  await screen.findByText("Links unavailable");
  fail = false;
  await act(async () =>
    fireEvent.submit(screen.getByRole("search", { name: "Link search" })),
  );
  await screen.findByText("Chapter 1");
  expect(requests()).toHaveLength(2);
});

test("entries without URLs remain readable and selectable, including merged members", async () => {
  const entry = {
    ...rows[0],
    url: "",
    members: [{ ...rows[0], url: "" }, rows[1]],
  };
  api.mockImplementation((path) =>
    Promise.resolve(
      path.includes("/links?") ? { ...result, links: [entry] } : item,
    ),
  );
  view();
  const read = await screen.findByRole("button", {
    name: "Mark read: Chapter 1",
  });
  expect(
    screen.queryByRole("link", { name: "Chapter 1" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Chapter 2/ })).toHaveAttribute(
    "href",
    rows[1].url,
  );
  expect(screen.getByLabelText("Select link: Chapter 1")).toBeEnabled();
  await click(read);
  expect(patch).toHaveBeenCalledWith("/links/c1", { read: true });
});

test("web links show useful gathered details without empty or title-only disclosures", async () => {
  const links = [
    {
      ...rows[0],
      title: "North Hall",
      context: "North Hall\nGrill\nBurger\nFries",
    },
    { ...rows[1], title: "South Hall", context: "  SOUTH\n HALL " },
    { ...rows[2], context: "  " },
    {
      ...rows[2],
      id: "summary",
      title: "Announcement",
      summary: "Doors open at noon.",
      context: "Doors open at noon.",
    },
  ];
  api.mockImplementation(async (path) =>
    path.includes("/links?")
      ? { links, total: links.length, sort_used: "source" }
      : { ...item, source_type: "web" },
  );
  view();
  const details = await screen.findByLabelText("Details for North Hall");
  expect(
    screen.queryByLabelText("Details for South Hall"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByLabelText("Details for Chapter 3"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByLabelText("Details for Announcement"),
  ).not.toBeInTheDocument();
  await click(details);
  expect(screen.getByText("Grill")).toBeVisible();
  expect(screen.getByText("Burger")).toBeVisible();
  expect(screen.getByText("Fries")).toBeVisible();
  expect(details.closest(".entry-details").parentElement).toHaveClass(
    "entry-row",
  );
});

test("merged web links retain each member's gathered details", async () => {
  const members = [
    { ...rows[0], title: "North Hall", context: "Grill\nBurger" },
    { ...rows[1], title: "South Hall", context: "Pasta bar\nRavioli" },
  ];
  api.mockImplementation(async (path) =>
    path.includes("/links?")
      ? { links: [{ ...members[0], members }], total: 1, sort_used: "source" }
      : { ...item, source_type: "web" },
  );
  view();
  await click(await screen.findByText("2 links in this entry"));
  await click(screen.getByLabelText("Details for North Hall"));
  await click(screen.getByLabelText("Details for South Hall"));
  expect(screen.getByText("Burger")).toBeVisible();
  expect(screen.getByText("Ravioli")).toBeVisible();
});
