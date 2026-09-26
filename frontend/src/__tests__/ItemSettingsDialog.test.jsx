import { vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { patch } from "../api";
import ItemSettingsDialog from "../Components/ItemSettingsDialog";

vi.mock("../api", () => ({ patch: vi.fn() }));

const item = {
  id: "source-item",
  title: "Reading list",
  source_type: "web",
  url: "https://example.org/series",
  auto_read: true,
};

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
  patch.mockImplementation(async (_, body) => ({ ...item, ...body }));
});

function settings(overrides = {}) {
  const onSaved = vi.fn();
  const onClose = vi.fn();
  render(
    <ItemSettingsDialog
      item={{ ...item, ...overrides }}
      onSaved={onSaved}
      onClose={onClose}
    />,
  );
  return { onSaved, onClose };
}

function change(label, value) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

async function click(label) {
  await act(async () =>
    fireEvent.click(screen.getByRole("button", { name: label })),
  );
}

test("legacy sources are displayed safely without creating a dirty settings form", async () => {
  settings();
  expect(screen.getByLabelText("Source URL 1")).toHaveValue(item.url);
  expect(screen.getByRole("link", { name: "Open source 1" })).toHaveAttribute(
    "href",
    item.url,
  );
  expect(screen.getByRole("link", { name: "Open source 1" })).toHaveAttribute(
    "rel",
    "noopener noreferrer",
  );
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  change("Source URL 1", `  ${item.url}  `);
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  change("Title", "New title");
  await click("Save changes");
  expect(patch).toHaveBeenCalledWith("/items/source-item", {
    title: "New title",
  });
});

test("adding a source saves the complete source list without scanning", async () => {
  const { onSaved } = settings({ source_urls: [item.url] });
  await click("Add source");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  change("Source URL 2", "https://other.example.org/feed");
  expect(
    screen.getByText(/Additional sources use automatic detection/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Changes apply on the next refresh/),
  ).toBeInTheDocument();
  await click("Save changes");
  expect(patch).toHaveBeenCalledExactlyOnceWith("/items/source-item", {
    source_urls: [item.url, "https://other.example.org/feed"],
  });
  expect(onSaved).toHaveBeenCalledTimes(1);
});

test("sources can be edited or all removed while saved content is retained", async () => {
  settings({ source_urls: [item.url, "https://other.example.org/feed"] });
  change("Source URL 1", "https://example.org/updated");
  await click("Remove source 2");
  await click("Save changes");
  expect(patch).toHaveBeenLastCalledWith("/items/source-item", {
    source_urls: ["https://example.org/updated"],
  });
  await click("Remove source 1");
  expect(screen.queryByLabelText("Source method")).not.toBeInTheDocument();
  await click("Save changes");
  expect(patch).toHaveBeenLastCalledWith("/items/source-item", {
    source_urls: [],
  });
});

test.each(["csv", "document", "manual"])(
  "%s items gain source controls only after adding a source",
  async (source_type) => {
    settings({ source_type, url: "import://saved-content" });
    expect(screen.queryByLabelText("Source URL 1")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Source method")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await click("Add source");
    change("Source URL 1", "https://example.org/updates");
    change(/Keywords/, "official");
    expect(screen.getByLabelText("Source method")).toHaveValue("auto");
    await click("Save changes");
    expect(patch).toHaveBeenLastCalledWith("/items/source-item", {
      source_urls: ["https://example.org/updates"],
      source_method: "auto",
      keywords: "official",
      selector: "",
      include_path: "",
    });
  },
);

test("an explicit empty source list overrides legacy web identity", () => {
  settings({ source_urls: [] });
  expect(screen.queryByLabelText("Source URL 1")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Source method")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
});

test("five sources is the maximum and removing one permits another", async () => {
  const source_urls = Array.from(
    { length: 5 },
    (_, i) => `https://example.org/source/${i}`,
  );
  settings({ source_urls });
  expect(screen.getByRole("button", { name: "Add source" })).toBeDisabled();
  await click("Remove source 3");
  expect(screen.getByRole("button", { name: "Add source" })).toBeEnabled();
  await click("Add source");
  expect(screen.getByLabelText("Source URL 5")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
});

test.each([
  "javascript:alert(1)",
  "https://user:password@example.org",
  "file:///tmp/secret",
  "not a URL",
])("invalid source %s cannot be opened or saved", async (url) => {
  settings();
  change("Source URL 1", url);
  expect(screen.getByLabelText("Source URL 1")).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(
    screen.queryByRole("link", { name: "Open source 1" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  await act(async () =>
    fireEvent.submit(screen.getByRole("dialog").querySelector("form")),
  );
  expect(patch).not.toHaveBeenCalled();
});

test("duplicate source URLs including fragment variants are rejected", async () => {
  settings();
  await click("Add source");
  change("Source URL 2", `${item.url}#chapters`);
  expect(
    screen.getByText("This source is already listed."),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  await click("Remove source 2");
  expect(
    screen.queryByText("This source is already listed."),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
});

test("failed source updates retain the draft for a retry", async () => {
  patch.mockRejectedValueOnce(new Error("Source is not public."));
  const { onSaved } = settings();
  change("Source URL 1", "https://different.example.org/list");
  await click("Save changes");
  expect(screen.getByRole("alert")).toHaveTextContent("Source is not public.");
  expect(screen.getByLabelText("Source URL 1")).toHaveValue(
    "https://different.example.org/list",
  );
  expect(onSaved).not.toHaveBeenCalled();
  await click("Save changes");
  expect(onSaved).toHaveBeenCalledTimes(1);
});
