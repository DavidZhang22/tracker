import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, post, uploadFile } from "../api";
import { PreferencesProvider } from "../Contexts/Preferences";
import AddPage from "../Pages/AddPage";
import ItemPage from "../Pages/ItemPage";
import { ImportDetails } from "../Components/ImportInput";

jest.mock("../api", () => ({
  api: jest.fn(),
  post: jest.fn(),
  uploadFile: jest.fn(),
  examples: [],
  day: () => "Sep 1",
  checked: () => "Today",
}));
const entry = {
  url: "https://example.org/job",
  title: "Example — Engineer",
  context: "Location: Remote",
  published_at: "2026-09-01T00:00:00+00:00",
  date_kind: "published",
  number: null,
  position: 0,
  method: "CSV import",
};
const preview = {
  scan_id: "csv-preview",
  source_type: "csv",
  source_name: "jobs.csv",
  title: "jobs",
  url: "csv:test",
  entries: [entry],
  warnings: [],
  methods: ["CSV import"],
  pages_scanned: 0,
  csv: {
    rows: 1,
    columns: [
      { index: 0, label: "URL" },
      { index: 1, label: "Title" },
    ],
    selected: { url: 0, title: 1, company: -1, date: -1, number: -1 },
    delimiter: ",",
    header: true,
  },
};
const file = new File(
  ["URL,Title\nhttps://example.org/job,Engineer"],
  "jobs.csv",
  { type: "text/csv" },
);
const item = {
  ...preview,
  id: "one",
  title: "Saved jobs",
  total_count: 1,
  dated_count: 1,
  read_count: 0,
  unread_count: 1,
  ignored_count: 0,
  new_count: 0,
  auto_read: false,
};
beforeEach(() => {
  jest.resetAllMocks();
  api.mockImplementation(async (path) =>
    path === "/settings"
      ? {}
      : path.includes("/links")
        ? { links: [{ ...entry, id: "link" }], total: 1 }
        : item,
  );
  uploadFile.mockResolvedValue(preview);
  post.mockResolvedValue({ id: "one" });
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
async function click(name) {
  const button = await screen.findByRole("button", { name });
  await act(async () => fireEvent.click(button));
}
async function chooseFile() {
  fireEvent.change(
    screen.getByLabelText("Import file", { selector: "input" }),
    {
      target: { files: [file] },
    },
  );
  await click("Preview links");
}

test("CSV previews rows inside one item and uses existing read choices", async () => {
  setup();
  await click("File or text");
  await chooseFile();
  expect(uploadFile).toHaveBeenCalledWith(file, { keywords: "" });
  expect(screen.getByLabelText("Link column")).toHaveValue("0");
  expect(screen.getByLabelText("Title column")).toHaveValue("1");
  expect(screen.getByText("1 links found")).toBeInTheDocument();
  expect(screen.getByText("Location: Remote")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Reading progress"), {
    target: { value: "choose" },
  });
  fireEvent.click(screen.getByLabelText("Read: Example — Engineer"));
  await click("Add to library");
  expect(post).toHaveBeenCalledWith("/items", {
    scan_id: "csv-preview",
    title: "jobs",
    mark_read: false,
    read_indices: [0],
  });
  expect(
    await screen.findByRole("link", { name: "Update import" }),
  ).toHaveAttribute("href", "/add?import=one");
  expect(
    screen.queryByRole("link", { name: /csv:test/ }),
  ).not.toBeInTheDocument();
});

test("changing columns or files invalidates the preview before saving", async () => {
  setup();
  await click("File or text");
  await chooseFile();
  fireEvent.change(screen.getByLabelText("Title column"), {
    target: { value: "-1" },
  });
  expect(
    screen.queryByRole("button", { name: "Add to library" }),
  ).not.toBeInTheDocument();
  await click("Update preview");
  expect(uploadFile).toHaveBeenLastCalledWith(file, {
    title_column: -1,
    keywords: "",
  });
  fireEvent.change(
    screen.getByLabelText("Import file", { selector: "input" }),
    {
      target: { files: [] },
    },
  );
  expect(
    screen.queryByRole("button", { name: "Add to library" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Preview links" })).toBeDisabled();
});

test("empty results and oversized files cannot be saved", async () => {
  setup();
  await click("File or text");
  fireEvent.change(
    screen.getByLabelText("Import file", { selector: "input" }),
    {
      target: { files: [new File(["x".repeat(4000001)], "huge.csv")] },
    },
  );
  expect(
    screen.getByText("Choose a non-empty file up to 4 MB."),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Preview links" })).toBeDisabled();
  expect(uploadFile).not.toHaveBeenCalled();
  uploadFile.mockResolvedValue({ ...preview, entries: [] });
  await chooseFile();
  expect(screen.getByRole("button", { name: "Add to library" })).toBeDisabled();
});

test("reupload targets existing CSV item without resetting read selections", async () => {
  setup("/add?import=one");
  await waitFor(() =>
    expect(
      screen.getByLabelText("Import file", { selector: "input" }),
    ).not.toBeDisabled(),
  );
  await chooseFile();
  expect(uploadFile).toHaveBeenCalledWith(file, {
    item_id: "one",
    keywords: "",
  });
  expect(screen.getByLabelText("Item name")).toHaveValue("Saved jobs");
  expect(screen.queryByLabelText("Reading progress")).not.toBeInTheDocument();
  await click("Update item");
  expect(post).toHaveBeenCalledWith("/items/one/import", {
    scan_id: "csv-preview",
    title: "Saved jobs",
  });
});

test("CSV cells are displayed as text, including formulas and HTML", () => {
  const { container } = render(
    <ImportDetails context={'<img src=x onerror="alert(1)">\n=SUM(1+1)'} />,
  );
  expect(container.querySelector("img")).toBeNull();
  expect(container.textContent).toContain("=SUM(1+1)");
});

test("upload errors keep the file available for retry", async () => {
  uploadFile.mockRejectedValueOnce(new Error("Database unavailable"));
  setup();
  await click("File or text");
  await chooseFile();
  expect(screen.getByText("Database unavailable")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Preview links" }),
  ).not.toBeDisabled();
  await click("Preview links");
  expect(screen.getByRole("button", { name: "Add to library" })).toBeEnabled();
});

test("pasted text previews one item and editing it invalidates the saved preview", async () => {
  uploadFile.mockResolvedValue({
    ...preview,
    source_type: "document",
    csv: undefined,
    document: { format: "TXT", candidates: 1 },
  });
  setup();
  await click("File or text");
  await click("Paste text");
  const input = screen.getByRole("textbox", { name: "Paste text" });
  expect(input).toHaveAttribute("maxLength", "200000");
  fireEvent.change(input, {
    target: { value: "Engineer https://example.org/job" },
  });
  await click("Preview links");
  const uploaded = uploadFile.mock.calls[0][0];
  expect(uploaded.name).toBe("Pasted links.txt");
  expect(uploaded.size).toBe(32);
  expect(screen.getByRole("button", { name: "Add to library" })).toBeEnabled();
  expect(screen.queryByLabelText("Link column")).not.toBeInTheDocument();
  fireEvent.change(input, {
    target: { value: "Other https://example.org/other" },
  });
  expect(
    screen.queryByRole("button", { name: "Add to library" }),
  ).not.toBeInTheDocument();
});

test("documents allow optional model filtering and never submit a website scan", async () => {
  uploadFile.mockResolvedValue({
    ...preview,
    source_type: "document",
    csv: undefined,
    document: { format: "PDF", candidates: 1 },
  });
  setup();
  await click("File or text");
  const document = new File(["%PDF-fixture"], "reading.pdf", {
    type: "application/pdf",
  });
  fireEvent.change(screen.getByLabelText("Import file"), {
    target: { files: [document] },
  });
  fireEvent.change(screen.getByLabelText("Links to import"), {
    target: { value: "content" },
  });
  await click("Preview links");
  expect(uploadFile).toHaveBeenCalledWith(document, {
    keywords: "",
    link_filter: "content",
  });
  expect(post).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Links to import"), {
    target: { value: "all" },
  });
  expect(
    screen.queryByRole("button", { name: "Add to library" }),
  ).not.toBeInTheDocument();
});
