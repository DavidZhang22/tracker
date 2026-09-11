import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Library } from "./Tracker";
import AddPage from "./Pages/AddPage";
import ItemPage from "./Pages/ItemPage";
import { LinkDate } from "./RowTools";
import { Notice } from "./Notice";
import { api, patch, post, refreshLibrary } from "./api";
jest.mock("./api", () => ({
  api: jest.fn(),
  patch: jest.fn(),
  post: jest.fn(),
  refreshLibrary: jest.fn(),
  examples: [],
  checked: () => "Today",
  day: () => "Sep 8, 2026",
}));
const item = {
  id: "one",
  url: "https://site.example",
  title: "My series",
  kind: "novel",
  favorite: false,
  ignored: false,
  auto_read: true,
  total_count: 2,
  ignored_count: 0,
  read_count: 0,
  unread_count: 2,
  new_count: 1,
  warnings: [],
  methods: ["page"],
  pages_scanned: 1,
  created_at: "2026-09-08",
  selector: "",
  include_path: "",
};
const link = {
  id: "chapter1",
  url: "https://site.example/chapter/1",
  title: "Chapter 1",
  read: false,
  favorite: false,
  ignored: false,
  is_new: true,
  number: 1,
};
beforeEach(() => jest.clearAllMocks());

test("all items includes ignored last, favorites first, and excludes Trash", async () => {
  api.mockResolvedValue([
    { ...item, id: "normal", title: "A normal source" },
    {
      ...item,
      id: "ignored",
      title: "An ignored favorite",
      favorite: true,
      ignored: true,
    },
    { ...item, id: "favorite", title: "Z favorite source", favorite: true },
    { ...item, id: "trash", title: "Trashed source", deleted: true },
  ]);
  render(
    <MemoryRouter>
      <Library />
    </MemoryRouter>,
  );
  await screen.findByText("Z favorite source");
  const titles = () =>
    screen
      .getAllByRole("article")
      .map((row) => row.querySelector(".item-title").textContent);
  expect(titles()).toEqual([
    "Z favorite source",
    "A normal source",
    "An ignored favorite",
  ]);
  expect(
    screen.getByRole("button", { name: "All items 3" }),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Sort items"), {
    target: { value: "title" },
  });
  expect(titles()).toEqual([
    "Z favorite source",
    "A normal source",
    "An ignored favorite",
  ]);
  expect(screen.queryByText("Trashed source")).not.toBeInTheDocument();
});

test("empty Trash has no example sources or refresh control", async () => {
  api.mockResolvedValue([]);
  render(
    <MemoryRouter initialEntries={["/?filter=trash"]}>
      <Library />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Trash is empty")).toBeInTheDocument();
  expect(screen.queryByText("Try a source")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Refresh all" }),
  ).not.toBeInTheDocument();
  expect(api).toHaveBeenCalledWith("/items?trash=true");
});

test("each refresh completion updates its row before the batch finishes", async () => {
  api.mockResolvedValue([item, { ...item, id: "slow", title: "Slow source" }]);
  let finish;
  const pending = new Promise((resolve) => {
    finish = resolve;
  });
  refreshLibrary.mockImplementation(async (receive) => {
    receive({ type: "start", total: 2 });
    receive({
      type: "item",
      item: { ...item, new_count: 10, unread_count: 12 },
      checked: 1,
      new_count: 10,
    });
    await pending;
    receive({ type: "complete", checked: 2, new_count: 10 });
  });
  render(
    <MemoryRouter>
      <Library />
    </MemoryRouter>,
  );
  await screen.findByText("My series");
  await click(screen.getByRole("button", { name: "Refresh all" }));
  expect(screen.getByText("12 unread")).toBeInTheDocument();
  expect(
    screen.getByText("10 new links. 1 item checked. Refreshing…"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Favorite My series" }),
  ).toBeEnabled();
  expect(screen.getByRole("button", { name: "Refreshing…" })).toBeDisabled();
  await act(async () => finish());
  expect(
    screen.getByText("10 new links. 2 items checked."),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Refresh all" })).toBeEnabled();
});

test("interrupted refresh keeps completed row updates and clears its busy status", async () => {
  api.mockResolvedValue([item]);
  refreshLibrary.mockImplementation(async (receive) => {
    receive({
      type: "item",
      item: { ...item, unread_count: 8 },
      checked: 1,
      new_count: 6,
    });
    throw new Error("Refresh connection closed. Completed updates were kept.");
  });
  render(
    <MemoryRouter>
      <Library />
    </MemoryRouter>,
  );
  await screen.findByText("My series");
  await click(screen.getByRole("button", { name: "Refresh all" }));
  expect(screen.getByText("8 unread")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Completed updates were kept.",
  );
  expect(screen.getByRole("button", { name: "Refresh all" })).toBeEnabled();
  expect(screen.queryByText(/items checked/)).not.toBeInTheDocument();
});
async function click(element) {
  await act(async () => {
    fireEvent.click(element);
  });
}
function detail() {
  render(
    <MemoryRouter initialEntries={["/items/one"]}>
      <Routes>
        <Route path="/items/:id" element={<ItemPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
function mockDetail(auto = true) {
  api.mockImplementation((path) =>
    Promise.resolve(
      path.includes("/links")
        ? { links: [link], total: 1, sort_used: "number" }
        : { ...item, auto_read: auto },
    ),
  );
  patch.mockResolvedValue(item);
}

test("right-click opens link actions without marking read, and favorite persists", async () => {
  mockDetail();
  post.mockResolvedValue({ updated: 1 });
  detail();
  const title = await screen.findByRole("link", { name: /Chapter 1/ });
  fireEvent.contextMenu(title);
  const favorite = await screen.findByRole("menuitem", { name: "Favorite" });
  expect(patch).not.toHaveBeenCalled();
  await click(favorite);
  expect(post).toHaveBeenCalledWith("/links/bulk", {
    ids: ["chapter1"],
    item_id: "one",
    action: "favorite",
  });
});

test("library row context menu can ignore and keyboard actions remain available", async () => {
  api.mockResolvedValue([item]);
  post.mockResolvedValue({ updated: 1 });
  render(
    <MemoryRouter>
      <Library />
    </MemoryRouter>,
  );
  fireEvent.contextMenu(await screen.findByText("My series"));
  await click(await screen.findByRole("menuitem", { name: "Ignore" }));
  expect(post).toHaveBeenCalledWith("/items/bulk", {
    ids: ["one"],
    action: "ignore",
  });
  await click(screen.getByRole("button", { name: "Actions for My series" }));
  expect(
    await screen.findByRole("menuitem", { name: "Delete" }),
  ).toBeInTheDocument();
});

test("selecting visible links enables bulk delete and explains Trash retention", async () => {
  mockDetail();
  post.mockResolvedValue({ updated: 1 });
  detail();
  await screen.findByText("Chapter 1");
  await click(screen.getByLabelText("Select this page"));
  await click(screen.getByRole("button", { name: "Delete", exact: true }));
  expect(post).toHaveBeenCalledWith("/links/bulk", {
    ids: ["chapter1"],
    item_id: "one",
    action: "delete",
  });
  expect(await screen.findByText(/1 links moved to Trash/)).toBeInTheDocument();
  expect(screen.queryByText("1 selected")).not.toBeInTheDocument();
});

test("changing a link filter clears bulk selection", async () => {
  mockDetail();
  detail();
  await screen.findByText("Chapter 1");
  await click(screen.getByLabelText("Select link: Chapter 1"));
  expect(screen.getByText("1 selected")).toBeInTheDocument();
  await click(screen.getByRole("button", { name: "Favorites", exact: true }));
  expect(screen.queryByText("1 selected")).not.toBeInTheDocument();
});

test("all matching selection keeps off-screen IDs for bulk actions", async () => {
  const second = { ...link, id: "chapter51", title: "Chapter 51", number: 51 };
  api.mockImplementation((path) =>
    Promise.resolve(
      path.includes("/links")
        ? {
            links: path.includes("offset=50") ? [second] : [link],
            total: 51,
            sort_used: "number",
          }
        : item,
    ),
  );
  post.mockImplementation((path) =>
    Promise.resolve(
      path.endsWith("link-selection")
        ? { ids: [link.id, second.id], total: 2 }
        : { updated: 2 },
    ),
  );
  detail();
  await screen.findByText("Chapter 1");
  await act(async () =>
    fireEvent.change(screen.getByLabelText("Selection options"), {
      target: { value: "all" },
    }),
  );
  expect(post).toHaveBeenCalledWith("/items/one/link-selection", {
    filter: "all",
    search: "",
    sort: "auto",
    direction: "asc",
  });
  expect(screen.getByText("2 selected")).toBeInTheDocument();
  await click(screen.getByRole("button", { name: "Next", exact: true }));
  await screen.findByText("Chapter 51");
  expect(screen.getByText("2 selected")).toBeInTheDocument();
  await click(screen.getByRole("button", { name: "Favorite", exact: true }));
  expect(post).toHaveBeenCalledWith("/links/bulk", {
    action: "favorite",
    item_id: "one",
    ids: [link.id, second.id],
  });
});

test("read ranges are offered for one selected link and use the displayed order", async () => {
  mockDetail();
  post.mockResolvedValue({ updated: 12 });
  detail();
  await screen.findByText("Chapter 1");
  await act(async () =>
    fireEvent.change(screen.getByLabelText("Order direction"), {
      target: { value: "desc" },
    }),
  );
  await click(screen.getByLabelText("Select link: Chapter 1"));
  await act(async () =>
    fireEvent.change(screen.getByLabelText("More bulk actions"), {
      target: { value: "read-before" },
    }),
  );
  expect(post).toHaveBeenCalledWith("/items/one/read-range", {
    anchor_id: "chapter1",
    side: "before",
    filter: "all",
    search: "",
    sort: "auto",
    direction: "desc",
  });
  expect(
    await screen.findByText(/12 links marked read in the current order/),
  ).toBeInTheDocument();
  fireEvent.contextMenu(screen.getByRole("link", { name: /Chapter 1/ }));
  await click(
    await screen.findByRole("menuitem", { name: "Mark all after as read" }),
  );
  expect(post).toHaveBeenLastCalledWith("/items/one/read-range", {
    anchor_id: "chapter1",
    side: "after",
    filter: "all",
    search: "",
    sort: "auto",
    direction: "desc",
  });
});

test("range actions are hidden for multiple selected links", async () => {
  api.mockImplementation((path) =>
    Promise.resolve(
      path.includes("/links")
        ? {
            links: [link, { ...link, id: "chapter2", title: "Chapter 2" }],
            total: 2,
          }
        : item,
    ),
  );
  detail();
  await screen.findByText("Chapter 1");
  await click(screen.getByLabelText("Select this page"));
  expect(
    screen.queryByRole("option", { name: "Mark all before as read" }),
  ).not.toBeInTheDocument();
});

test("bulk item selection sends all selected IDs and supports restoring Trash", async () => {
  api.mockResolvedValue([
    item,
    { ...item, id: "two", title: "Another series" },
  ]);
  post.mockResolvedValue({ updated: 2 });
  render(
    <MemoryRouter>
      <Library />
    </MemoryRouter>,
  );
  await screen.findByText("My series");
  await click(screen.getByLabelText("Select this page"));
  await click(screen.getByRole("button", { name: "Favorite", exact: true }));
  expect(post).toHaveBeenCalledWith("/items/bulk", {
    ids: ["one", "two"],
    action: "favorite",
  });
  api.mockResolvedValue([{ ...item, deleted: true }]);
  await click(screen.getByRole("button", { name: "Trash", exact: true }));
  expect(api).toHaveBeenCalledWith("/items?trash=true");
  await click(screen.getByLabelText("Select My series"));
  await click(screen.getByRole("button", { name: "Restore", exact: true }));
  expect(post).toHaveBeenCalledWith("/items/bulk", {
    ids: ["one"],
    action: "restore",
  });
});

test("dates disclose scheduled meaning, origin and time zone", () => {
  render(
    <LinkDate
      entry={{
        published_at: "2026-09-08T12:00:00+00:00",
        date_kind: "scheduled",
        date_precision: "time",
        date_source: "Contest API",
      }}
    />,
  );
  expect(screen.getByTitle(/Starts · Contest API/)).toBeInTheDocument();
  expect(screen.getByText(/Starts ·/)).toBeInTheDocument();
});

test("a cached refresh explains the ten-minute reuse window", async () => {
  mockDetail();
  post.mockResolvedValue({
    ok: true,
    cached: true,
    new_count: 0,
    checked_at: "2026-09-08T12:00:00Z",
  });
  detail();
  await screen.findByText("Chapter 1");
  await click(screen.getByRole("button", { name: "Refresh item" }));
  expect(
    await screen.findByText(/at most once every ten minutes/),
  ).toBeInTheDocument();
});

test("library filters new items and searches by title", async () => {
  api.mockResolvedValue([
    item,
    { ...item, id: "two", title: "Other series", new_count: 0 },
  ]);
  render(
    <MemoryRouter>
      <Library />
    </MemoryRouter>,
  );
  expect(await screen.findByText("My series")).toBeInTheDocument();
  await click(screen.getByRole("button", { name: /New content/ }));
  expect(screen.queryByText("Other series")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Search library"), {
    target: { value: "missing" },
  });
  expect(screen.getByText("No matching items")).toBeInTheDocument();
});

test("library favorites persist through the API", async () => {
  api.mockResolvedValue([item]);
  patch.mockResolvedValue({ ...item, favorite: true });
  render(
    <MemoryRouter>
      <Library />
    </MemoryRouter>,
  );
  await click(
    await screen.findByRole("button", { name: "Favorite My series" }),
  );
  await waitFor(() =>
    expect(patch).toHaveBeenCalledWith("/items/one", { favorite: true }),
  );
});

test("ignored library view can restore an item", async () => {
  api.mockResolvedValue([{ ...item, ignored: true }]);
  patch.mockResolvedValue(item);
  render(
    <MemoryRouter initialEntries={["/?filter=ignored"]}>
      <Library />
    </MemoryRouter>,
  );
  await click(await screen.findByRole("button", { name: "Restore My series" }));
  await waitFor(() =>
    expect(patch).toHaveBeenCalledWith("/items/one", { ignored: false }),
  );
});

test("scan preview saves the actual server scan with preferences", async () => {
  post.mockImplementation((path) =>
    Promise.resolve(
      path === "/scans"
        ? {
            scan_id: "scan1",
            title: "Found series",
            entries: [],
            kind: "novel",
            warnings: [],
            pages_scanned: 1,
          }
        : { id: "one" },
    ),
  );
  render(
    <MemoryRouter>
      <AddPage />
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByLabelText(/Source URL/), {
    target: { value: "https://site.example" },
  });
  expect(screen.queryByText("What gets tracked")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/Keywords/), {
    target: { value: "English, official" },
  });
  await click(screen.getByRole("button", { name: "Scan links" }));
  expect(post).toHaveBeenCalledWith("/scans", {
    url: "https://site.example",
    selector: "",
    include_path: "",
    keywords: "English, official",
  });
  expect(await screen.findByText("0 links found")).toBeInTheDocument();
  await click(screen.getByLabelText("Mark existing links as read"));
  const saveButton = screen.getByRole("button", { name: /Add to library/ });
  expect(saveButton.closest(".preview-sticky")).toHaveTextContent(
    "Review & save",
  );
  await click(saveButton);
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/items", {
      scan_id: "scan1",
      title: "Found series",
      mark_read: true,
      auto_read: true,
    }),
  );
});

test("addition cooldown counts down without losing the preview or preferences", async () => {
  jest.useFakeTimers();
  try {
    post
      .mockResolvedValueOnce({
        scan_id: "scan-cooldown",
        title: "Found series",
        entries: [],
        kind: "novel",
        warnings: [],
        pages_scanned: 1,
      })
      .mockRejectedValueOnce(
        Object.assign(new Error("You can add one item every 8 seconds."), {
          status: 429,
          retryAfter: 8,
        }),
      )
      .mockResolvedValueOnce({ id: "saved" });
    render(
      <MemoryRouter initialEntries={["/add"]}>
        <Routes>
          <Route path="/add" element={<AddPage />} />
          <Route path="/items/saved" element={<p>Saved item</p>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText(/Source URL/), {
      target: { value: "https://site.example" },
    });
    await click(screen.getByRole("button", { name: "Scan links" }));
    fireEvent.change(screen.getByLabelText("Item name"), {
      target: { value: "My title" },
    });
    await click(screen.getByLabelText("Mark existing links as read"));
    await click(screen.getByRole("button", { name: "Add to library" }));
    expect(screen.getByRole("button", { name: "Add in 8s" })).toBeDisabled();
    await click(screen.getByRole("button", { name: "Add in 8s" }));
    expect(post).toHaveBeenCalledTimes(2);
    act(() => jest.advanceTimersByTime(7999));
    expect(screen.getByRole("button", { name: "Add in 1s" })).toBeDisabled();
    expect(screen.getByLabelText("Item name")).toHaveValue("My title");
    expect(screen.getByLabelText("Mark existing links as read")).toBeChecked();
    act(() => jest.advanceTimersByTime(1));
    expect(
      screen.getByRole("button", { name: "Add to library" }),
    ).toBeEnabled();
    expect(post).toHaveBeenCalledTimes(2); // Never rescan or save automatically.
    await click(screen.getByRole("button", { name: "Add to library" }));
    expect(post).toHaveBeenLastCalledWith("/items", {
      scan_id: "scan-cooldown",
      title: "My title",
      mark_read: true,
      auto_read: true,
    });
    expect(screen.getByText("Saved item")).toBeInTheDocument();
    expect(jest.getTimerCount()).toBe(0);
  } finally {
    jest.useRealTimers();
  }
});

test("opening links marks them read when enabled", async () => {
  mockDetail();
  detail();
  const anchor = await screen.findByRole("link", { name: /Chapter 1/ });
  await click(anchor);
  await waitFor(() =>
    expect(patch).toHaveBeenCalledWith("/links/chapter1", { read: true }),
  );
  expect(anchor).toHaveAttribute("target", "_blank");
});

test("opening a link does not mark read when preference is off", async () => {
  mockDetail(false);
  detail();
  await click(await screen.findByRole("link", { name: /Chapter 1/ }));
  expect(patch).not.toHaveBeenCalled();
});

test("middle click marks read but right click does not", async () => {
  mockDetail();
  detail();
  const anchor = await screen.findByRole("link", { name: /Chapter 1/ });
  await act(async () => {
    fireEvent(anchor, new MouseEvent("auxclick", { bubbles: true, button: 2 }));
  });
  expect(patch).not.toHaveBeenCalled();
  await act(async () => {
    fireEvent(anchor, new MouseEvent("auxclick", { bubbles: true, button: 1 }));
  });
  expect(patch).toHaveBeenCalledWith("/links/chapter1", { read: true });
});

test("read preference and ignored link controls persist", async () => {
  mockDetail();
  detail();
  await click(await screen.findByLabelText("Mark as read when opened"));
  await waitFor(() =>
    expect(patch).toHaveBeenCalledWith("/items/one", { auto_read: false }),
  );
  await click(
    await screen.findByRole("button", { name: "Ignore link: Chapter 1" }),
  );
  await waitFor(() =>
    expect(patch).toHaveBeenCalledWith("/links/chapter1", { ignored: true }),
  );
});

test("failed refresh displays an actionable error without removing links", async () => {
  mockDetail();
  post.mockResolvedValue({ ok: false, error: "Source blocked. Retry later." });
  detail();
  await click(await screen.findByRole("button", { name: "Refresh item" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Source blocked. Retry later.",
  );
  expect(
    await screen.findByRole("link", { name: /Chapter 1/ }),
  ).toBeInTheDocument();
});

test("database outage shows retry instead of an empty collection", async () => {
  api.mockRejectedValueOnce(new Error("Storage is temporarily unavailable."));
  api.mockResolvedValueOnce([item]);
  render(
    <MemoryRouter>
      <Library />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Library unavailable")).toBeInTheDocument();
  expect(screen.queryByText("Start your collection")).not.toBeInTheDocument();
  await click(screen.getByRole("button", { name: "Retry loading" }));
  expect(await screen.findByText("My series")).toBeInTheDocument();
});

test("dismissed errors stay dismissed until a new message arrives", () => {
  const { rerender } = render(
    <Notice error>Source blocked. Retry later.</Notice>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Dismiss error" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  rerender(<Notice error>Source blocked. Retry later.</Notice>);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  rerender(<Notice error>Storage is unavailable.</Notice>);
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Storage is unavailable.",
  );
});

test("routine scan notes do not repeat on library rows", async () => {
  api.mockResolvedValue([
    { ...item, warnings: ["Some dates are unavailable."] },
  ]);
  render(
    <MemoryRouter>
      <Library />
    </MemoryRouter>,
  );
  expect(await screen.findByText("My series")).toBeInTheDocument();
  expect(
    screen.queryByText(/Scan notes|Open a row’s actions/),
  ).not.toBeInTheDocument();
});

test("failed checks remain dismissible", async () => {
  api.mockResolvedValue([{ ...item, error: "Source is unavailable." }]);
  render(
    <MemoryRouter>
      <Library />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Last check incomplete")).toBeInTheDocument();
  await click(
    screen.getByRole("button", { name: "Dismiss scan notes for My series" }),
  );
  expect(screen.queryByText("Last check incomplete")).not.toBeInTheDocument();
});

test.each([true, false])(
  "latest shortcut opens the returned link and respects auto-read=%s",
  async (auto_read) => {
    const latest_link = { ...link, title: "Latest chapter", id: "latest" };
    api.mockResolvedValue([{ ...item, auto_read, latest_link }]);
    patch.mockResolvedValue({ ok: true });
    render(
      <MemoryRouter>
        <Library />
      </MemoryRouter>,
    );
    const latest = await screen.findByRole("link", {
      name: /Latest entry for My series/,
    });
    expect(latest).toHaveAttribute("href", latest_link.url);
    expect(latest).toHaveAttribute("target", "_blank");
    fireEvent.contextMenu(latest);
    expect(patch).not.toHaveBeenCalled();
    await click(latest);
    if (auto_read)
      expect(patch).toHaveBeenCalledWith("/links/latest", { read: true });
    else expect(patch).not.toHaveBeenCalled();
  },
);

test("read status is a labeled text action", async () => {
  mockDetail();
  detail();
  const status = await screen.findByRole("button", {
    name: "Mark read: Chapter 1",
  });
  expect(status).toHaveTextContent("Unread");
  await click(status);
  expect(patch).toHaveBeenCalledWith("/links/chapter1", { read: true });
});

test("publication meaning and time zone remain in date details instead of every row summary", () => {
  const { container } = render(
    <LinkDate
      entry={{
        published_at: "2026-09-09T13:30:00Z",
        date_kind: "published",
        date_precision: "time",
        date_source: "Chapter list",
      }}
    />,
  );
  const summary = container.querySelector("summary");
  expect(summary).not.toHaveTextContent(/Published|EDT/);
  expect(container.querySelector(".date-origin")).toHaveTextContent(
    /Published · Chapter list/,
  );
  expect(container.querySelector(".date-origin")).toHaveTextContent(
    /time zone/,
  );
});

test("item scan details contain only methods and date coverage", async () => {
  api.mockImplementation((path) =>
    Promise.resolve(
      path.includes("/links")
        ? { links: [], total: 0, sort_used: "source" }
        : {
            ...item,
            pages_scanned: 3,
            methods: [
              "page",
              "application table",
              "link classifier",
              "GitHub README",
            ],
            dated_count: 0,
            total_count: 895,
          },
    ),
  );
  detail();
  const summary = await screen.findByText("Scan details", { exact: true });
  const section = summary.closest("details");
  expect(section.querySelectorAll("p")).toHaveLength(2);
  expect(section).toHaveTextContent(
    "3 pages · page, application table, link classifier, GitHub README",
  );
  expect(section).toHaveTextContent("0 of 895 links have a source date.");
  expect(section).not.toHaveTextContent(/Refresh|Checked|New badges|Requests/);
});
