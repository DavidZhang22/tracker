import { StrictMode } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { AuthBoundary } from "../Auth/Auth";
import { PreferencesProvider, defaults } from "../Contexts/Preferences";
import { StartupDataProvider } from "../Contexts/StartupData";
import LibraryPage from "../Pages/LibraryPage";
import ItemPage from "../Pages/ItemPage";
import { api } from "../api";

vi.mock("../api", () => ({
  api: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  refreshLibrary: vi.fn(),
  checked: () => "Today",
  day: () => "Today",
  examples: [],
}));

const item = {
  id: "one",
  title: "A series",
  url: "https://example.org/series",
  kind: "novel",
  read_count: 1,
  total_count: 2,
  unread_count: 1,
  ignored_count: 0,
  new_count: 0,
  created_at: "2026-09-20",
  methods: [],
};
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function view(path = "/", strict = false) {
  const tree = (
    <MemoryRouter initialEntries={[path]}>
      <AuthBoundary>
        <StartupDataProvider>
          <Link to="/other">Leave page</Link>
          <PreferencesProvider>
            <Routes>
              <Route path="/" element={<LibraryPage />} />
              <Route path="/items/:id" element={<ItemPage />} />
              <Route path="/other" element={<p>Other page</p>} />
            </Routes>
          </PreferencesProvider>
        </StartupDataProvider>
      </AuthBoundary>
    </MemoryRouter>
  );
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree);
}
const calls = (path) => api.mock.calls.filter(([value]) => value === path);
beforeEach(() => vi.resetAllMocks());

test("Library data and preferences load together, then saved sorting applies without fetching data twice", async () => {
  const settings = deferred();
  const items = deferred();
  api.mockImplementation((path) =>
    path === "/auth/status"
      ? Promise.resolve({ required: true, user: { id: "david" } })
      : path === "/settings"
        ? settings.promise
        : items.promise,
  );
  const { container } = view();
  await waitFor(() => expect(calls("/items")).toHaveLength(1));
  expect(calls("/settings")).toHaveLength(1);
  await act(async () =>
    items.resolve([
      { ...item, id: "z", title: "Z series", created_at: "2026-09-21" },
      item,
    ]),
  );
  expect(screen.queryByText("A series")).not.toBeInTheDocument();
  await act(async () =>
    settings.resolve({ ...defaults, library_sort: "title" }),
  );
  await screen.findByText("A series");
  expect(
    [...container.querySelectorAll(".item-title")].map(
      (node) => node.textContent,
    ),
  ).toEqual(["A series", "Z series"]);
  expect(calls("/items")).toHaveLength(1);
});

test("Trash startup fetches both required lists only once", async () => {
  const settings = deferred();
  api.mockImplementation((path) =>
    Promise.resolve(
      path === "/auth/status"
        ? { required: false }
        : path === "/settings"
          ? settings.promise
          : path === "/items?trash=true"
            ? [
                {
                  ...item,
                  id: "deleted",
                  title: "Deleted series",
                  deleted: true,
                },
              ]
            : [item],
    ),
  );
  view("/?filter=trash");
  await waitFor(() => expect(calls("/items?trash=true")).toHaveLength(1));
  expect(calls("/items")).toHaveLength(1);
  await act(async () => settings.resolve(defaults));
  await screen.findByText("Deleted series");
  expect(screen.queryByText("A series")).not.toBeInTheDocument();
  expect(calls("/items")).toHaveLength(1);
  expect(calls("/items?trash=true")).toHaveLength(1);
});

test("Item metadata starts before preferences but link requests use the saved order", async () => {
  const settings = deferred();
  api.mockImplementation((path) =>
    Promise.resolve(
      path === "/auth/status"
        ? { required: false }
        : path === "/settings"
          ? settings.promise
          : path === "/items/one"
            ? item
            : { links: [], total: 0 },
    ),
  );
  view("/items/one");
  await waitFor(() => expect(calls("/items/one")).toHaveLength(1));
  expect(api.mock.calls.some(([path]) => path.includes("/links?"))).toBe(false);
  await act(async () =>
    settings.resolve({
      ...defaults,
      link_sort: "number",
      link_direction: "asc",
    }),
  );
  await screen.findByRole("heading", { name: "A series" });
  expect(calls("/items/one")).toHaveLength(1);
  expect(
    api.mock.calls.filter(([path]) => path.includes("/links?")),
  ).toHaveLength(1);
  const path = api.mock.calls.find(([path]) => path.includes("/links?"))[0];
  expect(path).toContain("sort=number");
  expect(path).toContain("direction=asc");
});

test("a preload rejected while preferences load is handled and Retry makes a fresh request", async () => {
  const settings = deferred();
  api.mockImplementation((path) =>
    path === "/auth/status"
      ? Promise.resolve({ required: false })
      : path === "/settings"
        ? settings.promise
        : calls("/items").length === 1
          ? Promise.reject(new Error("Temporary outage"))
          : Promise.resolve([item]),
  );
  view();
  await waitFor(() => expect(calls("/items")).toHaveLength(1));
  await act(async () => settings.resolve(defaults));
  fireEvent.click(await screen.findByRole("button", { name: "Retry loading" }));
  await screen.findByText("A series");
  expect(calls("/items")).toHaveLength(2);
});

test("changing accounts aborts even a consumed pending preload and discards late results", async () => {
  let account = "first";
  const firstItems = deferred();
  let firstSignal;
  api.mockImplementation((path, options) =>
    path === "/auth/status"
      ? Promise.resolve({
          required: true,
          user: { id: account, username: "same" },
        })
      : path === "/settings"
        ? Promise.resolve(defaults)
        : account === "first"
          ? ((firstSignal = options.signal), firstItems.promise)
          : Promise.resolve([{ ...item, title: "Second account series" }]),
  );
  view();
  await screen.findByText("Loading your library…");
  expect(calls("/items")).toHaveLength(1);
  expect(firstSignal.aborted).toBe(false);
  account = "second";
  await act(async () =>
    window.dispatchEvent(new Event("trackify:unauthorized")),
  );
  await screen.findByText("Second account series");
  expect(firstSignal.aborted).toBe(true);
  await act(async () => firstItems.resolve([item]));
  expect(screen.queryByText("A series")).not.toBeInTheDocument();
  expect(calls("/items")).toHaveLength(2);
});

test("leaving the startup route aborts pending unused page data without starting unrelated requests", async () => {
  const settings = deferred();
  let signal;
  api.mockImplementation((path, options) =>
    path === "/auth/status"
      ? Promise.resolve({ required: false })
      : path === "/settings"
        ? settings.promise
        : ((signal = options.signal), new Promise(() => {})),
  );
  view();
  await waitFor(() => expect(calls("/items")).toHaveLength(1));
  fireEvent.click(screen.getByRole("link", { name: "Leave page" }));
  expect(signal.aborted).toBe(true);
  await act(async () => settings.resolve(defaults));
  await screen.findByText("Other page");
  expect(calls("/items")).toHaveLength(1);
});

test("StrictMode cleanup aborts the discarded startup request and unmount aborts its replacement", async () => {
  const signals = [];
  api.mockImplementation((path, options) =>
    path === "/auth/status"
      ? Promise.resolve({ required: false })
      : path === "/settings"
        ? new Promise(() => {})
        : (signals.push(options.signal), new Promise(() => {})),
  );
  const { unmount } = view("/", true);
  await waitFor(() => expect(signals.length).toBe(2));
  expect(signals[0].aborted).toBe(true);
  expect(signals[1].aborted).toBe(false);
  unmount();
  expect(signals.every((signal) => signal.aborted)).toBe(true);
});
