import { vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import ContinueLink from "../Components/ContinueLink";
import LibraryPage from "../Pages/LibraryPage";
import ItemPage from "../Pages/ItemPage";
import { libraryView, libraryViewParams } from "../libraryViews";
import { api, patch } from "../api";

vi.mock("../api", () => ({
  api: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  refreshLibrary: vi.fn(),
  examples: [],
  checked: () => "Today",
  day: () => "Sep 20, 2026",
}));
const view = {
  query: "series",
  filter: "unread",
  kind: "novel",
  sort: "title",
  search_mode: "local",
};
const item = {
  id: "one",
  title: "My series",
  url: "https://example.com/series",
  kind: "novel",
  created_at: "2026-09-20",
  favorite: true,
  ignored: false,
  read_count: 1,
  unread_count: 2,
  total_count: 3,
  ignored_count: 0,
  new_count: 1,
  warnings: [],
  methods: ["page"],
  auto_read: true,
};
const entry = {
  id: "two",
  title: "Chapter 2",
  url: "https://example.com/chapter/2",
  read: false,
  link_count: 1,
};

beforeEach(() => vi.resetAllMocks());

function Navigation() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="location">
        {location.pathname}
        {location.search}
        {location.hash}
      </output>
      <button onClick={() => navigate(-1)}>Back in history</button>
      <button onClick={() => navigate(1)}>Forward in history</button>
    </>
  );
}

test("Library URL values are bounded and validated, with preference defaults", () => {
  const result = libraryView(
    new URLSearchParams({
      q: "a".repeat(300),
      filter: "bad",
      kind: "invalid",
      sort: "wrong",
      mode: "remote",
    }),
    "title",
  );
  expect(result).toEqual({
    query: "a".repeat(200),
    filter: "all",
    kind: "all",
    sort: "title",
    search_mode: "semantic",
  });
  const params = libraryViewParams(
    new URLSearchParams("release=abc&q=series&kind=novel"),
    { filter: "favorites" },
  );
  expect(params.get("release")).toBe("abc");
  expect(libraryView(params)).toEqual({
    ...view,
    filter: "favorites",
    sort: "recent",
    search_mode: "semantic",
  });
});

test("Library controls follow URLs and browser history without losing other criteria", async () => {
  api.mockImplementation((path) =>
    Promise.resolve(path === "/items" ? [item] : []),
  );
  render(
    <MemoryRouter
      initialEntries={[
        "/?q=series&filter=unread&kind=novel&sort=title&mode=local",
      ]}
    >
      <LibraryPage />
      <Navigation />
    </MemoryRouter>,
  );
  await screen.findByText("My series");
  expect(screen.getByLabelText("Search library")).toHaveValue("series");
  expect(screen.getByLabelText("Media type")).toHaveValue("novel");
  expect(screen.getByLabelText("Sort items")).toHaveValue("title");
  expect(screen.getByLabelText("Search mode")).toHaveValue("local");
  fireEvent.click(screen.getByRole("button", { name: /^Favorites\s*1$/ }));
  expect(screen.getByTestId("location")).toHaveTextContent(
    "q=series&filter=favorites&kind=novel&sort=title&mode=local",
  );
  fireEvent.click(screen.getByRole("button", { name: "Back in history" }));
  expect(screen.getByRole("button", { name: /^Unread\s*1$/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  fireEvent.click(screen.getByRole("button", { name: "Forward in history" }));
  expect(
    screen.getByRole("button", { name: /^Favorites\s*1$/ }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByText("Saved views")).not.toBeInTheDocument();
  expect(api).not.toHaveBeenCalledWith("/views");
});

test.each([true, false])(
  "Continue opens a single entry and honors auto-read=%s",
  async (auto_read) => {
    patch.mockResolvedValue({ ok: true });
    const onRead = vi.fn();
    render(
      <MemoryRouter>
        <ContinueLink
          item={{ ...item, auto_read, next_unread: entry }}
          onRead={onRead}
        />
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", {
      name: "Continue My series: Chapter 2",
    });
    expect(link).toHaveAttribute("href", entry.url);
    expect(link).toHaveAttribute("target", "_blank");
    fireEvent.contextMenu(link);
    expect(patch).not.toHaveBeenCalled();
    await act(async () => fireEvent.click(link));
    if (auto_read) {
      expect(patch).toHaveBeenCalledWith("/links/two", { read: true });
      expect(onRead).toHaveBeenCalledOnce();
    } else expect(patch).not.toHaveBeenCalled();
  },
);

test.each([
  { ...entry, link_count: 3 },
  { ...entry, url: null },
])(
  "merged and linkless Continue only navigates to unread entries",
  async (next_unread) => {
    render(
      <MemoryRouter>
        <ContinueLink item={{ ...item, next_unread }} />
        <Navigation />
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: /^Continue My series/ });
    expect(link).toHaveAttribute(
      "href",
      "/items/one?filter=unread&sort=auto&direction=asc#entry-two",
    );
    expect(link).not.toHaveAttribute("target");
    await act(async () => fireEvent.click(link));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "filter=unread&sort=auto&direction=asc",
    );
    expect(patch).not.toHaveBeenCalled();
  },
);

test.each([{ deleted: true }, { ignored: true }, { next_unread: null }])(
  "Continue is absent for unavailable next entries",
  (overrides) => {
    render(
      <MemoryRouter>
        <ContinueLink item={{ ...item, next_unread: entry, ...overrides }} />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  },
);

test("item view honors and validates linked controls; Continue is absent in Trash", async () => {
  api.mockImplementation((path) =>
    Promise.resolve(
      path.includes("/links")
        ? { links: [], total: 0, sort_used: "source" }
        : { ...item, next_unread: entry },
    ),
  );
  render(
    <MemoryRouter
      initialEntries={[
        "/items/one?filter=trash&sort=invalid&direction=asc&offset=-50",
      ]}
    >
      <Routes>
        <Route path="/items/:id" element={<ItemPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: "My series" });
  expect(
    screen.getByRole("button", { name: "Trash", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByLabelText("Order links by")).toHaveValue("auto");
  expect(screen.getByLabelText("Order direction")).toHaveValue("asc");
  expect(
    screen.queryByRole("link", { name: /^Continue/ }),
  ).not.toBeInTheDocument();
  expect(
    api.mock.calls.some(([path]) =>
      path.includes("filter=trash&sort=auto&direction=asc&search=&offset=0"),
    ),
  ).toBe(true);
});

test("Latest opens a merged entry in the tracker without marking its group read", async () => {
  api.mockResolvedValue([
    { ...item, latest_link: { ...entry, link_count: 3 } },
  ]);
  render(
    <MemoryRouter>
      <LibraryPage />
      <Navigation />
    </MemoryRouter>,
  );
  const latest = await screen.findByRole("link", {
    name: "Latest entry for My series: Chapter 2",
  });
  expect(latest).toHaveAttribute("href", "/items/one?search=Chapter%202");
  expect(latest).not.toHaveAttribute("target");
  await act(async () => fireEvent.click(latest));
  expect(screen.getByTestId("location")).toHaveTextContent(
    "/items/one?search=Chapter%202",
  );
  expect(patch).not.toHaveBeenCalled();
});

test("Continue preserves the resolved sort, page and target row", () => {
  render(
    <MemoryRouter>
      <ContinueLink
        item={{
          ...item,
          next_unread: { ...entry, link_count: 2, sort: "date", offset: 100 },
        }}
      />
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("link", { name: /^Continue My series/ }),
  ).toHaveAttribute(
    "href",
    "/items/one?filter=unread&sort=date&direction=asc&offset=100#entry-two",
  );
});

test.each(["entry-two", "unrelated"])(
  "item landing scrolls only to a row in the loaded page: %s",
  async (hash) => {
    const original = HTMLElement.prototype.scrollIntoView;
    const scroll = vi.fn();
    HTMLElement.prototype.scrollIntoView = scroll;
    try {
      api.mockImplementation((path) =>
        Promise.resolve(
          path.includes("/links")
            ? { links: [entry], total: 101, sort_used: "date" }
            : {
                ...item,
                next_unread: {
                  ...entry,
                  link_count: 2,
                  sort: "date",
                  offset: 100,
                },
              },
        ),
      );
      render(
        <MemoryRouter
          initialEntries={[
            `/items/one?filter=unread&sort=date&direction=asc&offset=100#${hash}`,
          ]}
        >
          <Routes>
            <Route path="/items/:id" element={<ItemPage />} />
          </Routes>
        </MemoryRouter>,
      );
      await screen.findByRole("button", { name: "Mark read: Chapter 2" });
      expect(document.getElementById("entry-two")).toBeInTheDocument();
      expect(
        api.mock.calls.some(([path]) =>
          path.includes(
            "filter=unread&sort=date&direction=asc&search=&offset=100",
          ),
        ),
      ).toBe(true);
      if (hash === "entry-two") {
        await waitFor(() => expect(scroll).toHaveBeenCalledOnce());
        expect(scroll.mock.contexts[0]).toBe(
          document.getElementById("entry-two"),
        );
        expect(scroll).toHaveBeenCalledWith({ block: "center" });
      } else expect(scroll).not.toHaveBeenCalled();
      expect(patch).not.toHaveBeenCalled();
    } finally {
      if (original) HTMLElement.prototype.scrollIntoView = original;
      else delete HTMLElement.prototype.scrollIntoView;
    }
  },
);
