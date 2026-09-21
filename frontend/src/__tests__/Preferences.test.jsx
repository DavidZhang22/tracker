import { vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, patch, post } from "../api";
import {
  defaults,
  orderedPreview,
  PreferencesProvider,
} from "../Contexts/Preferences";
import SettingsPage from "../Pages/SettingsPage";
import AddPage from "../Pages/AddPage";
import ItemPage from "../Pages/ItemPage";
import ItemSettingsDialog from "../Components/ItemSettingsDialog";
import { RefreshControl } from "../Components/RowTools";

vi.mock("../api", () => ({
  api: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
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
beforeAll(() => {
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterAll(() => vi.unstubAllGlobals());
beforeEach(() => {
  vi.resetAllMocks();
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
      const values = { ...body };
      delete values.apply_auto_read;
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

test("general settings retain account defaults without loading item settings", async () => {
  const view = settings();
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
  const restored = settings();
  expect(await screen.findByLabelText("Default link order")).toHaveValue("asc");
  expect(
    screen.getByLabelText("Mark links as read when opened in new items"),
  ).not.toBeChecked();
  expect(screen.queryByText("Choose an item")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Item settings" }),
  ).not.toBeInTheDocument();
  expect(api.mock.calls.some(([path]) => path.startsWith("/items"))).toBe(
    false,
  );
  restored.unmount();
  settings("/items/one");
  await waitFor(() =>
    expect(
      api.mock.calls.some(
        ([path]) =>
          path.includes("/items/one/links?") && path.includes("direction=asc"),
      ),
    ).toBe(true),
  );
});

test("legacy item settings URLs open the editor on the item page", async () => {
  settings("/settings?item=one");
  await click(
    await screen.findByLabelText("Mark as read when opened for this item"),
  );
  change(/Keywords/, "Spanish, official");
  await click(screen.getByText("Advanced content detection"));
  change("Content selector", "article a");
  change("URL must contain", "/chapter/");
  await click(screen.getByRole("button", { name: "Save changes" }));
  expect(patch).toHaveBeenCalledWith("/items/one", {
    auto_read: false,
    keywords: "Spanish, official",
    selector: "article a",
    include_path: "/chapter/",
  });
});

test("manual media type saves independently of the scraping method and can return to automatic", async () => {
  settings("/settings?item=one");
  await screen.findByLabelText("Media type");
  change("Media type", "comic");
  await click(screen.getByRole("button", { name: "Save changes" }));
  expect(patch).toHaveBeenLastCalledWith(
    "/items/one",
    expect.objectContaining({ kind_override: "comic" }),
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await click(screen.getByRole("button", { name: "Item settings" }));
  expect(await screen.findByLabelText("Media type")).toHaveValue("comic");
  change("Media type", "");
  await click(screen.getByRole("button", { name: "Save changes" }));
  expect(patch).toHaveBeenLastCalledWith(
    "/items/one",
    expect.objectContaining({ kind_override: "" }),
  );
});

test("preview media type is saved and reset when scanning another source", async () => {
  post.mockResolvedValue({
    ...item,
    kind: "comic",
    detected_kind: "comic",
    scan_id: "media-preview",
    entries: [],
    warnings: [],
  });
  render(
    <MemoryRouter>
      <AddPage />
    </MemoryRouter>,
  );
  change(/Source URL/, "https://example.org/series");
  await click(screen.getByRole("button", { name: "Scan links" }));
  expect(
    await screen.findByRole("option", { name: "Automatic (Manga & comics)" }),
  ).toBeInTheDocument();
  change("Media type", "novel");
  await click(screen.getByRole("button", { name: "Add to library" }));
  expect(post).toHaveBeenLastCalledWith(
    "/items",
    expect.objectContaining({ kind_override: "novel" }),
  );
  await click(screen.getByRole("button", { name: "Scan links" }));
  expect(screen.getByLabelText("Media type")).toHaveValue("");
});

test("source method defaults persist and item methods save independently", async () => {
  const view = settings();
  await screen.findByLabelText("Default source method");
  change("Default source method", "sitemap");
  await click(screen.getByRole("button", { name: "Save preferences" }));
  expect(saved.source_method).toBe("sitemap");
  view.unmount();
  api.mockImplementation(async (path) =>
    path === "/settings"
      ? saved
      : path.includes("/links")
        ? { links: [], total: 0 }
        : { ...item, selector: "article a" },
  );
  settings("/items/one?settings=1");
  expect(await screen.findByLabelText("Source method")).toHaveValue("auto");
  await click(screen.getByText("Advanced content detection"));
  change("Source method", "wordpress_com");
  expect(screen.queryByLabelText("Content selector")).not.toBeInTheDocument();
  await click(screen.getByRole("button", { name: "Save changes" }));
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

test("a pending save cannot update another item's editor after navigation", async () => {
  let finishSave;
  patch.mockImplementation(
    () =>
      new Promise((resolve) => {
        finishSave = resolve;
      }),
  );
  const onSaved = vi.fn();
  const view = render(
    <ItemSettingsDialog
      key="one"
      item={item}
      onSaved={onSaved}
      onClose={vi.fn()}
    />,
  );
  change("Title", "Renamed first item");
  await click(screen.getByRole("button", { name: "Save changes" }));
  const second = { ...item, id: "two", title: "Second item", auto_read: false };
  view.rerender(
    <ItemSettingsDialog
      key="two"
      item={second}
      onSaved={onSaved}
      onClose={vi.fn()}
    />,
  );
  await act(async () => finishSave({ ...item, title: "Renamed first item" }));
  expect(screen.getByLabelText("Title")).toHaveValue("Second item");
  expect(
    screen.getByLabelText("Mark as read when opened for this item"),
  ).not.toBeChecked();
  expect(onSaved).not.toHaveBeenCalled();
});

test("renaming in the popup updates the heading without reloading links", async () => {
  settings("/items/one?filter=unread&search=chapter&direction=asc");
  await click(await screen.findByRole("button", { name: "Item settings" }));
  expect(
    await screen.findByRole("dialog", { name: "Item settings" }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Title")).toHaveFocus();
  const requestsBefore = api.mock.calls.length;
  change("Title", "  My reading list  ");
  await click(screen.getByRole("button", { name: "Save changes" }));
  expect(patch).toHaveBeenLastCalledWith("/items/one", {
    title: "My reading list",
  });
  expect(
    await screen.findByRole("heading", { name: "My reading list" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Search links")).toHaveValue("chapter");
  expect(api).toHaveBeenCalledTimes(requestsBefore);
  await click(screen.getByRole("button", { name: "Item settings" }));
  expect(await screen.findByLabelText("Title")).toHaveValue("My reading list");
});

test("cancel discards title edits and unchanged or blank titles cannot be saved", async () => {
  settings("/items/one?settings=1");
  await screen.findByLabelText("Title");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  change("Title", "   ");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  change("Title", "Discard this edit");
  await click(screen.getByRole("button", { name: "Cancel" }));
  expect(patch).not.toHaveBeenCalled();
  expect(screen.getByRole("heading", { name: item.title })).toBeInTheDocument();
  await click(screen.getByRole("button", { name: "Item settings" }));
  expect(await screen.findByLabelText("Title")).toHaveValue(item.title);
});

test("failed item saving keeps the draft open for retry and prevents duplicate submissions", async () => {
  settings("/items/one?settings=1");
  await screen.findByLabelText("Title");
  change("Title", "Keep my edit");
  let reject;
  patch.mockImplementationOnce(
    () =>
      new Promise((_, fail) => {
        reject = fail;
      }),
  );
  await click(screen.getByRole("button", { name: "Save changes" }));
  expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  await act(async () => reject(new Error("Storage unavailable")));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Storage unavailable",
  );
  expect(screen.getByLabelText("Title")).toHaveValue("Keep my edit");
  await click(screen.getByRole("button", { name: "Save changes" }));
  expect(
    await screen.findByRole("heading", { name: "Keep my edit" }),
  ).toBeInTheDocument();
});

test("description edit opens the same popup and can restore the source description", async () => {
  const described = {
    ...item,
    description: "My notes",
    description_override: "My notes",
    description_auto: "Source summary",
  };
  api.mockImplementation(async (path) =>
    path === "/settings"
      ? saved
      : path.includes("/links")
        ? { links: [], total: 0 }
        : described,
  );
  settings("/items/one");
  await click(await screen.findByRole("button", { name: "Edit", exact: true }));
  expect(
    await screen.findByRole("textbox", { name: "Description" }),
  ).toHaveFocus();
  await click(screen.getByRole("button", { name: "Use source description" }));
  expect(screen.getByRole("textbox", { name: "Description" })).toHaveValue(
    "Source summary",
  );
  await click(screen.getByRole("button", { name: "Save changes" }));
  expect(patch).toHaveBeenLastCalledWith("/items/one", {
    description_override: null,
  });
});

test("imported items can be renamed without exposing web detection settings", async () => {
  const onSaved = vi.fn();
  render(
    <ItemSettingsDialog
      item={{ ...item, source_type: "document" }}
      onSaved={onSaved}
      onClose={vi.fn()}
    />,
  );
  expect(screen.queryByLabelText("Source method")).not.toBeInTheDocument();
  expect(
    screen.queryByText("Advanced content detection"),
  ).not.toBeInTheDocument();
  change("Title", "Uploaded reading list");
  await click(screen.getByRole("button", { name: "Save changes" }));
  expect(patch).toHaveBeenLastCalledWith("/items/one", {
    title: "Uploaded reading list",
  });
  expect(onSaved).toHaveBeenCalledWith(
    expect.objectContaining({ title: "Uploaded reading list" }),
  );
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
  const refresh = vi.fn();
  render(
    <MemoryRouter>
      <PreferencesProvider>
        <RefreshControl label="Refresh" onRefresh={refresh} />
      </PreferencesProvider>
    </MemoryRouter>,
  );
  await click(await screen.findByRole("button", { name: "Refresh" }));
  expect(refresh).toHaveBeenLastCalledWith(true);
  await click(screen.getByRole("button", { name: "Options for refresh" }));
  await click(
    await screen.findByRole("menuitem", { name: "Lightweight Refresh" }),
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

test("preview saves separate read choices for entries with no URLs", async () => {
  post.mockImplementation(async (path) =>
    path === "/scans"
      ? {
          ...item,
          scan_id: "list-scan",
          warnings: [],
          entries: [
            { title: "Chapter 1", url: "", source_id: "first", number: 1 },
            { title: "Chapter 2", url: "", source_id: "second", number: 2 },
          ],
        }
      : { id: "saved" },
  );
  render(
    <MemoryRouter>
      <AddPage />
    </MemoryRouter>,
  );
  change(/Source URL/, item.url);
  await click(screen.getByRole("button", { name: "Scan links" }));
  expect(await screen.findByText("2 entries found")).toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: /Chapter/ }),
  ).not.toBeInTheDocument();
  change("Reading progress", "choose");
  await click(screen.getByLabelText("Read: Chapter 2"));
  await click(screen.getByRole("button", { name: "Add to library" }));
  expect(post).toHaveBeenLastCalledWith(
    "/items",
    expect.objectContaining({ read_indices: [1] }),
  );
});
