import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, patch, post } from "./api";
import { defaults, orderedPreview, PreferencesProvider } from "./Preferences";
import SettingsPage from "./Pages/SettingsPage";
import AddPage from "./Pages/AddPage";
import ItemPage from "./Pages/ItemPage";
import { RefreshControl } from "./RowTools";

jest.mock("./api", () => ({
  api: jest.fn(),
  patch: jest.fn(),
  post: jest.fn(),
  examples: [],
  checked: () => "Today",
  day: () => "Sep 13",
}));
const item = {
  id: "one",
  title: "Test series",
  selector: "",
  include_path: "",
  keywords: "English",
  auto_read: true,
  total_count: 31,
  read_count: 0,
  unread_count: 31,
  ignored_count: 0,
  new_count: 0,
  methods: [],
  pages_scanned: 1,
  url: "https://example.org/series",
};
let saved;
beforeEach(() => {
  jest.resetAllMocks();
  saved = { ...defaults };
  api.mockImplementation(async (path) =>
    path === "/settings"
      ? { ...saved }
      : path === "/items"
        ? [item]
        : path.includes("/links")
          ? { links: [], total: 0 }
          : item,
  );
  patch.mockImplementation(async (path, body) => {
    if (path === "/settings") {
      const { apply_auto_read, ...values } = body;
      saved = { ...saved, ...values };
      return { ...saved };
    }
    return { ...item, ...body };
  });
});
async function click(target) {
  await act(async () => fireEvent.click(target));
}
function change(label, value) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
function settings(path = "/settings") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <PreferencesProvider>
        <Routes>
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/items/:id" element={<ItemPage />} />
        </Routes>
      </PreferencesProvider>
    </MemoryRouter>,
  );
}

test("saved order and reading defaults survive remount; item settings link back to the chosen item", async () => {
  const view = settings("/settings?item=one");
  expect(await screen.findByLabelText("Default link order")).toHaveValue(
    "desc",
  );
  change("Default link order", "asc");
  change("Default library sort", "title");
  await click(
    screen.getByLabelText("Mark links as read when opened in new items"),
  );
  await click(screen.getByRole("button", { name: "Save preferences" }));
  expect(await screen.findByText("Preferences saved.")).toBeInTheDocument();
  expect(patch).toHaveBeenCalledWith("/settings", {
    ...defaults,
    link_direction: "asc",
    library_sort: "title",
    auto_read: false,
    apply_auto_read: false,
  });
  view.unmount();
  settings("/settings?item=one");
  expect(await screen.findByLabelText("Default link order")).toHaveValue("asc");
  expect(
    screen.getByLabelText("Mark links as read when opened in new items"),
  ).not.toBeChecked();
  await click(await screen.findByRole("link", { name: "Back to item" }));
  await waitFor(() =>
    expect(
      api.mock.calls.some(
        ([path]) =>
          path.includes("/items/one/links?") && path.includes("direction=asc"),
      ),
    ).toBe(true),
  );
});

test("per-item read-on-open and detection settings are saved in one place", async () => {
  settings("/settings?item=one");
  await click(
    await screen.findByLabelText("Mark as read when opened for this item"),
  );
  change(/Keywords/, "Spanish, official");
  await click(screen.getByText("Advanced link detection"));
  change("Link selector", "article a");
  change("URL must contain", "/chapter/");
  await click(screen.getByRole("button", { name: "Save item settings" }));
  expect(patch).toHaveBeenCalledWith("/items/one", {
    source_method: "auto",
    auto_read: false,
    keywords: "Spanish, official",
    selector: "article a",
    include_path: "/chapter/",
  });
});

test("source method defaults persist and item methods save independently", async () => {
  settings("/settings?item=one");
  await screen.findByLabelText("Source method");
  change("Default source method", "sitemap");
  await click(screen.getByRole("button", { name: "Save preferences" }));
  expect(saved.source_method).toBe("sitemap");
  expect(screen.getByLabelText("Source method")).toHaveValue("auto");
  await click(screen.getByText("Advanced link detection"));
  change("Link selector", "article a");
  change("Source method", "wordpress_com");
  expect(screen.queryByLabelText("Link selector")).not.toBeInTheDocument();
  await click(screen.getByRole("button", { name: "Save item settings" }));
  expect(patch).toHaveBeenLastCalledWith(
    "/items/one",
    expect.objectContaining({ source_method: "wordpress_com", selector: "" }),
  );
});

test("add uses the saved method and changing it clears the old preview", async () => {
  saved.source_method = "sitemap";
  post.mockResolvedValue({
    ...item,
    scan_id: "preview",
    entries: [{ url: "https://example.org/post", title: "Post" }],
    warnings: [],
  });
  render(
    <MemoryRouter>
      <PreferencesProvider>
        <AddPage />
      </PreferencesProvider>
    </MemoryRouter>,
  );
  await screen.findByLabelText("Source method");
  expect(screen.getByLabelText("Source method")).toHaveValue("sitemap");
  change(/Source URL/, "https://example.org/");
  await click(screen.getByRole("button", { name: "Scan links" }));
  expect(post).toHaveBeenLastCalledWith(
    "/scans",
    expect.objectContaining({ source_method: "sitemap" }),
  );
  expect(
    await screen.findByRole("button", { name: "Add to library" }),
  ).toBeInTheDocument();
  change("Source method", "wordpress_com");
  expect(
    screen.queryByRole("button", { name: "Add to library" }),
  ).not.toBeInTheDocument();
  await click(screen.getByRole("button", { name: "Scan links" }));
  expect(post).toHaveBeenLastCalledWith(
    "/scans",
    expect.objectContaining({ source_method: "wordpress_com", selector: "" }),
  );
});

test("missing API credentials keep the explicit choice and show a dismissible error", async () => {
  post.mockRejectedValue(
    new Error("YouTube API needs a server API key. Choose Automatic for now."),
  );
  render(
    <MemoryRouter>
      <AddPage />
    </MemoryRouter>,
  );
  change(/Source URL/, "https://youtube.com/@example");
  change("Source method", "youtube");
  await click(screen.getByRole("button", { name: "Scan links" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "needs a server API key",
  );
  expect(screen.getByLabelText("Source method")).toHaveValue("youtube");
  expect(post).toHaveBeenCalledTimes(1);
  await click(screen.getByRole("button", { name: /Dismiss/ }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("failed settings save retains edits and never claims success", async () => {
  settings();
  await screen.findByText("Reading & library");
  change("Default link order", "asc");
  patch.mockRejectedValueOnce(new Error("Storage is temporarily unavailable."));
  await click(screen.getByRole("button", { name: "Save preferences" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Storage is temporarily unavailable",
  );
  expect(screen.getByLabelText("Default link order")).toHaveValue("asc");
  expect(screen.queryByText("Preferences saved.")).not.toBeInTheDocument();
  expect(saved.link_direction).toBe("desc");
});

test("failed preference loading can retry without falling back to unsaved defaults", async () => {
  api.mockRejectedValueOnce(new Error("Settings unavailable"));
  settings();
  await click(await screen.findByRole("button", { name: "Retry settings" }));
  expect(await screen.findByLabelText("Default link order")).toHaveValue(
    "desc",
  );
});

test("a saved deep default still offers an explicit lightweight override", async () => {
  saved.refresh_mode = "deep";
  const refresh = jest.fn();
  render(
    <PreferencesProvider>
      <RefreshControl label="Refresh all" onRefresh={refresh} />
    </PreferencesProvider>,
  );
  await click(await screen.findByRole("button", { name: "Refresh all" }));
  expect(refresh).toHaveBeenLastCalledWith(true);
  await click(screen.getByRole("button", { name: "Options for refresh all" }));
  await click(
    await screen.findByRole("menuitem", { name: "Lightweight refresh all" }),
  );
  expect(refresh).toHaveBeenLastCalledWith(false);
});

test("preview read selection survives pagination and a failed save, using original scan indices", async () => {
  const entries = Array.from({ length: 31 }, (_, i) => ({
    title: `Chapter ${i + 1}`,
    number: i + 1,
    url: `https://example.org/chapter/${i + 1}`,
  }));
  let attempts = 0;
  post.mockImplementation(async (path) => {
    if (path === "/scans")
      return { ...item, entries, warnings: [], scan_id: "scan1" };
    if (++attempts === 1) throw new Error("Storage unavailable. Try again.");
    return { id: "saved" };
  });
  render(
    <MemoryRouter initialEntries={["/add"]}>
      <Routes>
        <Route path="/add" element={<AddPage />} />
        <Route path="/items/saved" element={<p>Saved item</p>} />
      </Routes>
    </MemoryRouter>,
  );
  change(/Source URL/, item.url);
  await click(screen.getByRole("button", { name: "Scan links" }));
  expect(
    (await screen.findAllByRole("link", { name: /Chapter/ }))[0],
  ).toHaveTextContent("Chapter 31");
  change("Reading progress", "choose");
  await click(screen.getByLabelText("Read: Chapter 31"));
  await click(screen.getByLabelText("Read: Chapter 7"));
  await click(screen.getByRole("button", { name: "Next", exact: true }));
  await click(screen.getByLabelText("Read: Chapter 1"));
  await click(screen.getByRole("button", { name: "Previous", exact: true }));
  expect(screen.getByLabelText("Read: Chapter 31")).toBeChecked();
  expect(screen.getByText("3 read · 28 unread")).toBeInTheDocument();
  await click(screen.getByRole("button", { name: "Add to library" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Storage unavailable",
  );
  expect(screen.getByLabelText("Read: Chapter 7")).toBeChecked();
  await click(screen.getByRole("button", { name: "Add to library" }));
  expect(post).toHaveBeenLastCalledWith("/items", {
    scan_id: "scan1",
    title: item.title,
    mark_read: false,
    read_indices: [0, 6, 30],
  });
  expect(await screen.findByText("Saved item")).toBeInTheDocument();
});

test("rescanning resets read selection instead of assigning it to different content", async () => {
  post.mockResolvedValue({
    ...item,
    entries: [{ url: item.url + "/1", title: "One", number: 1 }],
    warnings: [],
    scan_id: "scan",
  });
  render(
    <MemoryRouter>
      <AddPage />
    </MemoryRouter>,
  );
  change(/Source URL/, item.url);
  await click(screen.getByRole("button", { name: "Scan links" }));
  change("Reading progress", "choose");
  await click(screen.getByLabelText("Read: One"));
  await click(screen.getByRole("button", { name: "Scan links" }));
  expect(screen.getByLabelText("Reading progress")).toHaveValue("unread");
  expect(screen.getByText("0 read · 1 unread")).toBeInTheDocument();
});

test("preview sorting honors dates, decimal numbers, missing fields, and source order", () => {
  const entries = [
    { title: "A", number: 2, published_at: "2026-09-10", url: "a" },
    { title: "B", number: 10.5, published_at: "2025-01-01", url: "b" },
    { title: "C", number: 9, published_at: null, url: "c" },
  ];
  const order = (settings, hint) =>
    orderedPreview(entries, { ...defaults, ...settings }, hint).map(
      ({ index }) => index,
    );
  expect(order({})).toEqual([1, 2, 0]);
  expect(order({ link_direction: "asc" })).toEqual([0, 2, 1]);
  expect(order({ link_sort: "date" })).toEqual([0, 1, 2]);
  expect(order({ link_sort: "date", link_direction: "asc" })).toEqual([
    1, 0, 2,
  ]);
  expect(order({}, "source")).toEqual([2, 1, 0]);
});
