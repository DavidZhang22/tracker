import { vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, post, uploadFile } from "../api";
import { PreferencesProvider } from "../Contexts/Preferences";
import AddPage from "../Pages/AddPage";
import ItemPage from "../Pages/ItemPage";

vi.mock("../api", () => ({
  api: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  uploadFile: vi.fn(),
  examples: [],
  day: () => "Sep 26",
  checked: () => "Today",
}));
const entry = {
  title: "Merge Intervals",
  url: "",
  context: "Sort by start, then merge",
  number: 56,
  published_at: null,
  position: 0,
  source_id: "manual:56",
};
const preview = {
  scan_id: "manual-preview",
  source_type: "document",
  source_name: "Manual entries",
  title: "Manual entries",
  url: "document:manual",
  entries: [entry],
  warnings: [],
  methods: ["Manual entries"],
  pages_scanned: 0,
  kind: "other",
};
let item;
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
  item = {
    id: "one",
    title: "Study list",
    url: "https://example.org/study",
    source_urls: ["https://example.org/study"],
    source_type: "web",
    kind: "blog",
    total_count: 1,
    unread_count: 1,
    read_count: 0,
    ignored_count: 0,
    new_count: 0,
    methods: [],
    warnings: [],
  };
  api.mockImplementation(async (path) =>
    path === "/settings"
      ? {}
      : path.includes("/links")
        ? { links: [{ ...entry, id: "saved" }], total: 1 }
        : item,
  );
  post.mockImplementation(async (path) =>
    path === "/scans/manual" ? preview : item,
  );
  uploadFile.mockResolvedValue({
    ...preview,
    document: { format: "TXT", candidates: 1 },
  });
});
function setup(path = "/add") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <PreferencesProvider>
        <Routes>
          <Route path="/add" element={<AddPage />} />
          <Route path="/items/:id" element={<ItemPage />} />
        </Routes>
      </PreferencesProvider>
    </MemoryRouter>,
  );
}
async function click(name, scope = screen) {
  const control = await scope.findByRole("button", { name });
  await act(async () => fireEvent.click(control));
}
function fill(title = "Merge Intervals") {
  fireEvent.change(screen.getByLabelText("Entry 1 title"), {
    target: { value: title },
  });
  fireEvent.change(screen.getByLabelText("Entry 1 details"), {
    target: { value: "Sort by start, then merge" },
  });
}

test("manual items preview without a link and retain read choices before saving", async () => {
  setup();
  await click("Manual");
  expect(
    screen.getByRole("button", { name: "Preview entries" }),
  ).toBeDisabled();
  fill();
  await click("Preview entries");
  expect(post).toHaveBeenCalledWith("/scans/manual", {
    entries: [
      {
        title: "Merge Intervals",
        context: "Sort by start, then merge",
        url: "",
        published_at: null,
        number: null,
      },
    ],
  });
  expect(screen.getByText("1 entry found")).toBeInTheDocument();
  expect(
    screen.getByText("Sort by start, then merge", { selector: "p" }),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Item name"), {
    target: { value: "Interview practice" },
  });
  fireEvent.change(screen.getByLabelText("Reading progress"), {
    target: { value: "all" },
  });
  await click("Add to library");
  expect(post).toHaveBeenCalledWith("/items", {
    scan_id: "manual-preview",
    title: "Interview practice",
    mark_read: true,
    read_indices: [],
  });
});

test("manual optional link, date and number are included without fetching the link", async () => {
  setup();
  await click("Manual");
  fill();
  fireEvent.change(screen.getByLabelText("Entry 1 link"), {
    target: { value: "https://example.org/problem" },
  });
  fireEvent.change(screen.getByLabelText("Entry 1 date"), {
    target: { value: "2026-09-26" },
  });
  fireEvent.change(screen.getByLabelText("Entry 1 number"), {
    target: { value: "56" },
  });
  await click("Preview entries");
  expect(post).toHaveBeenCalledWith("/scans/manual", {
    entries: [
      expect.objectContaining({
        url: "https://example.org/problem",
        published_at: "2026-09-26T00:00:00Z",
        number: 56,
      }),
    ],
  });
  expect(post.mock.calls.map(([path]) => path)).toEqual(["/scans/manual"]);
});

test("adding, removing, or editing records invalidates a reviewed preview", async () => {
  setup();
  await click("Manual");
  fill();
  await click("Preview entries");
  await click("Another entry");
  expect(
    screen.queryByRole("button", { name: "Add to library" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Preview entries" }),
  ).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Entry 2 title"), {
    target: { value: "Course Schedule" },
  });
  await click("Preview entries");
  expect(post.mock.calls.at(-1)[1].entries).toHaveLength(2);
  await click("Remove entry 1");
  expect(screen.getByLabelText("Entry 1 title")).toHaveValue("Course Schedule");
  expect(
    screen.queryByRole("button", { name: "Add to library" }),
  ).not.toBeInTheDocument();
  await click("Preview entries");
  fireEvent.change(screen.getByLabelText("Entry 1 details"), {
    target: { value: "DFS" },
  });
  expect(
    screen.queryByRole("button", { name: "Add to library" }),
  ).not.toBeInTheDocument();
});

test("existing items require preview before appending manual entries", async () => {
  setup("/items/one");
  await click("Add entry");
  const dialog = within(screen.getByRole("dialog", { name: "Add entries" }));
  expect(dialog.getByRole("button", { name: "Add to item" })).toBeDisabled();
  expect(
    dialog.getByRole("link", { name: "Import file or text" }),
  ).toHaveAttribute("href", "/add?append=one");
  fill();
  await click("Preview entries", dialog);
  expect(post).toHaveBeenCalledWith("/scans/manual", {
    item_id: "one",
    entries: [expect.objectContaining({ title: "Merge Intervals", url: "" })],
  });
  expect(
    dialog.getByRole("region", { name: "Entry preview" }),
  ).toHaveTextContent("Sort by start, then merge");
  await click("Add to item", dialog);
  expect(post).toHaveBeenCalledWith("/items/one/entries", {
    scan_id: "manual-preview",
  });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(await screen.findByText("Entries added.")).toBeInTheDocument();
});

test("a failed append retains the preview for retry", async () => {
  setup("/items/one");
  await click("Add entry");
  fill();
  await click("Preview entries");
  post.mockRejectedValueOnce(new Error("Database temporarily unavailable"));
  await click("Add to item");
  expect(
    screen.getByText("Database temporarily unavailable"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add to item" })).toBeEnabled();
  await click("Add to item");
  expect(await screen.findByText("Entries added.")).toBeInTheDocument();
});

test("manual preview errors retain the typed content", async () => {
  setup();
  await click("Manual");
  fill();
  post.mockRejectedValueOnce(new Error("You can add up to 4,999 entries."));
  await click("Preview entries");
  expect(
    screen.getByText("You can add up to 4,999 entries."),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Entry 1 title")).toHaveValue("Merge Intervals");
  expect(
    screen.queryByRole("button", { name: "Add to library" }),
  ).not.toBeInTheDocument();
});

test("file and pasted text append to web items without replacing their source or title", async () => {
  setup("/add?append=one");
  await waitFor(() =>
    expect(
      screen.getByLabelText("Import file", { selector: "input" }),
    ).toBeEnabled(),
  );
  await click("Paste text");
  fireEvent.change(screen.getByRole("textbox", { name: "Paste text" }), {
    target: { value: "Merge Intervals,Sort by start then merge" },
  });
  await click("Preview entries");
  expect(uploadFile).toHaveBeenCalledWith(expect.any(File), {
    item_id: "one",
    append: true,
    keywords: "",
  });
  expect(screen.queryByLabelText("Item name")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Reading progress")).not.toBeInTheDocument();
  expect(screen.getByText("1 entry found")).toBeInTheDocument();
  await click("Add to item");
  expect(post).toHaveBeenCalledWith("/items/one/entries", {
    scan_id: "manual-preview",
  });
});

test("Trash items cannot receive manual entries or imports", async () => {
  item.deleted = true;
  const first = setup("/items/one");
  await screen.findByRole("heading", { name: "Study list" });
  expect(
    screen.queryByRole("button", { name: "Add entry" }),
  ).not.toBeInTheDocument();
  first.unmount();
  setup("/add?append=one");
  expect(
    await screen.findByText("Choose an item outside Trash to add entries."),
  ).toBeInTheDocument();
  expect(
    screen.getByLabelText("Import file", { selector: "input" }),
  ).toBeDisabled();
});

test("imported items with added sources show the source and refresh control", async () => {
  item.source_type = "document";
  item.url = "document:manual";
  setup("/items/one");
  expect(
    await screen.findByRole("link", { name: "example.org" }),
  ).toHaveAttribute("href", "https://example.org/study");
  expect(
    screen.getByRole("button", { name: "Refresh item" }),
  ).toBeInTheDocument();
});

test("web items with all sources removed do not offer refresh", async () => {
  item.source_urls = [];
  setup("/items/one");
  await screen.findByRole("heading", { name: "Study list" });
  expect(
    screen.queryByRole("button", { name: "Refresh item" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: "example.org" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add entry" })).toBeEnabled();
});
