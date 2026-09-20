import { vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthBoundary } from "../Auth/Auth";
import { defaults, PreferencesProvider } from "../Contexts/Preferences";
import { Shell } from "../Components/Shell";
import Description from "../Components/Description";
import { api, refreshLibrary } from "../api";

vi.mock("../api", () => ({
  api: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  refreshLibrary: vi.fn(),
  examples: [],
  checked: () => "Today",
}));
beforeEach(() => vi.resetAllMocks());
const item = {
  id: "one",
  title: "A series",
  url: "https://example.org/series",
  kind: "novel",
  created_at: "2026-09-20",
  read_count: 1,
  total_count: 2,
  unread_count: 1,
  ignored_count: 0,
  new_count: 0,
};

function setup() {
  return render(
    <MemoryRouter>
      <AuthBoundary>
        <PreferencesProvider>
          <Shell />
        </PreferencesProvider>
      </AuthBoundary>
    </MemoryRouter>,
  );
}

test("document startup keeps a responsive shell while auth and settings resolve", async () => {
  let authenticate, preferences;
  api.mockImplementation((path) =>
    path === "/auth/status"
      ? new Promise((resolve) => {
          authenticate = resolve;
        })
      : path === "/settings"
        ? new Promise((resolve) => {
            preferences = resolve;
          })
        : Promise.resolve([item]),
  );
  const { container } = setup();
  expect(screen.getByRole("status")).toHaveTextContent("Loading…");
  expect(
    container.querySelector(".startup-shell.app-shell"),
  ).toBeInTheDocument();
  expect(container.querySelector(".auth-page")).toBeNull();
  expect(screen.getAllByRole("contentinfo")).toHaveLength(1);
  expect(screen.getByRole("contentinfo").parentElement).toHaveClass(
    "workspace",
  );
  await act(async () =>
    authenticate({ required: true, user: { username: "david" } }),
  );
  expect(screen.getByRole("status")).toHaveTextContent("Loading preferences…");
  expect(api).not.toHaveBeenCalledWith("/items");
  await act(async () => preferences(defaults));
  await screen.findByText("A series");
  expect(container.querySelector(".startup-shell")).toBeNull();
  expect(screen.getAllByRole("contentinfo")).toHaveLength(1);
  expect(screen.getByRole("contentinfo").parentElement).toHaveClass(
    "workspace",
  );
});

test("background Library refresh keeps mounted rows and does not reload auth or preferences", async () => {
  api.mockImplementation(async (path) =>
    path === "/auth/status"
      ? { required: true, user: { username: "david" } }
      : path === "/settings"
        ? defaults
        : [item],
  );
  let complete;
  refreshLibrary.mockImplementation(
    (receive) =>
      new Promise((resolve) => {
        receive({ type: "start", total: 1 });
        complete = () => {
          receive({
            type: "item",
            item: { ...item, unread_count: 2, new_count: 1 },
            checked: 1,
            new_count: 1,
          });
          receive({ type: "complete", checked: 1, new_count: 1 });
          resolve();
        };
      }),
  );
  const { container } = setup();
  const row = (await screen.findByText("A series")).closest("article");
  fireEvent.click(screen.getByRole("button", { name: "Refresh", exact: true }));
  await waitFor(() => expect(refreshLibrary).toHaveBeenCalledOnce());
  expect(screen.getByText("A series").closest("article")).toBe(row);
  expect(container.querySelector(".startup-shell")).toBeNull();
  expect(screen.queryByText("Loading your library…")).not.toBeInTheDocument();
  expect(
    api.mock.calls.filter(([path]) => path === "/auth/status"),
  ).toHaveLength(1);
  expect(api.mock.calls.filter(([path]) => path === "/settings")).toHaveLength(
    1,
  );
  await act(async () => complete());
  expect(screen.getByText("2 unread")).toBeInTheDocument();
  expect(screen.getByText("A series").closest("article")).toBe(row);
});

test("suppressed automatic descriptions remain distinct from missing and manual descriptions", () => {
  const { rerender } = render(
    <MemoryRouter>
      <Description preview item={{ description_suppressed: true }} />
    </MemoryRouter>,
  );
  expect(screen.getByText("Automatic description hidden.")).toBeInTheDocument();
  rerender(
    <MemoryRouter>
      <Description
        item={{
          id: "one",
          description_suppressed: true,
          description: "My own description",
        }}
      />
    </MemoryRouter>,
  );
  expect(screen.getByText("My own description")).toBeInTheDocument();
  expect(
    screen.queryByText("Automatic description hidden."),
  ).not.toBeInTheDocument();
});
