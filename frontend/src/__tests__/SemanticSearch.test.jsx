import { vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import useLibrarySearch from "../Hooks/useLibrarySearch";
import Description from "../Components/Description";
import { librarySearchIndex } from "../Search/searchIndex";
import { api } from "../api";
import { createSearchHandler } from "../Search/workerHandler";

vi.mock("../api", () => ({ api: vi.fn() }));
const items = [
  {
    id: "a",
    title: "Grand Blue",
    url: "https://a.example",
    description: "A comedy about college life and scuba diving.",
  },
  {
    id: "b",
    title: "Python Bytes",
    url: "https://b.example",
    description: "A programming podcast with interviews.",
  },
];
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { resolve, promise };
};
function Harness({ query, rows = items, trash = false, mode = "semantic" }) {
  const { scores, updating } = useLibrarySearch(rows, query, trash, mode);
  return (
    <output aria-busy={updating}>
      {rows
        .filter((row) => scores.get(row.id) > 0)
        .map((row) => row.title)
        .join(", ")}
    </output>
  );
}
beforeEach(() => {
  vi.useFakeTimers();
  api.mockReset();
});
afterEach(() => {
  vi.clearAllTimers();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
const tick = async () =>
  act(async () => {
    vi.advanceTimersByTime(251);
  });

test("debounces semantic requests and reuses completed queries", async () => {
  api.mockResolvedValue({ scores: [{ id: "a", score: 0.8 }], semantic: true });
  const view = render(<Harness query="underw" />);
  view.rerender(<Harness query="underwater" />);
  expect(api).not.toHaveBeenCalled();
  await tick();
  expect(api).toHaveBeenCalledTimes(1);
  expect(JSON.parse(api.mock.calls[0][1].body)).toEqual({
    query: "underwater",
    trash: false,
  });
  expect(screen.getByRole("status")).toHaveTextContent("Grand Blue");
  expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "false");
  view.rerender(<Harness query="" />);
  view.rerender(<Harness query="underwater" />);
  await tick();
  expect(api).toHaveBeenCalledTimes(1);
});

test("retains completed results while typing and discards stale responses", async () => {
  const first = deferred(),
    second = deferred();
  api
    .mockResolvedValueOnce({ scores: [{ id: "a", score: 1 }] })
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  const view = render(<Harness query="ocean" />);
  await tick();
  view.rerender(<Harness query="news" />);
  await tick();
  expect(screen.getByRole("status")).toHaveTextContent("Grand Blue");
  expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
  view.rerender(<Harness query="listen" />);
  expect(api.mock.calls[1][1].signal.aborted).toBe(true);
  await tick();
  await act(async () => second.resolve({ scores: [{ id: "b", score: 1 }] }));
  await act(async () => first.resolve({ scores: [{ id: "a", score: 1 }] }));
  expect(screen.getByRole("status")).toHaveTextContent("Python Bytes");
  expect(screen.getByRole("status")).not.toHaveTextContent("Grand Blue");
});

test("metadata edits and switching Trash invalidate cached searches", async () => {
  api.mockResolvedValue({ scores: [{ id: "a", score: 1 }] });
  const view = render(<Harness query="college" />);
  await tick();
  view.rerender(
    <Harness
      query="college"
      rows={[{ ...items[0], description: "Changed" }, items[1]]}
    />,
  );
  await tick();
  expect(api).toHaveBeenCalledTimes(2);
  view.rerender(<Harness query="college" trash />);
  await tick();
  expect(JSON.parse(api.mock.calls[2][1].body).trash).toBe(true);
});

test("server failure keeps local typo matching available", async () => {
  api.mockRejectedValue(new Error("Unavailable"));
  render(<Harness query="grnad blue" />);
  await tick();
  expect(screen.getByRole("status")).toHaveTextContent("Grand Blue");
  expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "false");
  expect(librarySearchIndex(items)("pyhton bytes").get("b")).toBeGreaterThan(0);
  expect(librarySearchIndex(items)("scuba diving").get("a")).toBeGreaterThan(0);
});

test("malformed search responses settle on local matches instead of leaving selection pending", async () => {
  api.mockResolvedValue({ scores: null });
  render(<Harness query="grand blue" />);
  await tick();
  expect(screen.getByRole("status")).toHaveTextContent("Grand Blue");
  expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "false");
});

test("descriptions are plain text with an accessible edit action", () => {
  const edit = vi.fn();
  const text = '<img src=x onerror="alert(1)"> A source description.';
  const view = render(
    <MemoryRouter>
      <Description item={{ ...items[0], description: text }} onEdit={edit} />
    </MemoryRouter>,
  );
  expect(screen.getByText(text)).toBeVisible();
  expect(view.container.querySelector("img")).toBeNull();
  expect(screen.getByRole("button", { name: "Edit" })).toHaveAttribute(
    "aria-haspopup",
    "dialog",
  );
  screen.getByRole("button", { name: "Edit" }).click();
  expect(edit).toHaveBeenCalledOnce();
});

test("normalized equivalent queries and reading changes reuse one semantic request", async () => {
  api.mockResolvedValue({ scores: [{ id: "a", score: 1 }] });
  const view = render(<Harness query="grand blue" />);
  await tick();
  view.rerender(
    <Harness
      query="  ＧＲＡＮＤ   BLUE  "
      rows={items.map((row) => ({ ...row, favorite: true, unread_count: 8 }))}
    />,
  );
  await tick();
  expect(api).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "false");
});

test("quick search uses current local typo and description matches without a request", async () => {
  const view = render(<Harness query="pyhton bytes" mode="local" />);
  expect(screen.getByRole("status")).toHaveTextContent("Python Bytes");
  expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "false");
  view.rerender(<Harness query="scuba diving" mode="local" />);
  expect(screen.getByRole("status")).toHaveTextContent("Grand Blue");
  await tick();
  expect(api).not.toHaveBeenCalled();
});

test("single-character prefixes stay local while two-character concepts can use semantic search", async () => {
  api.mockResolvedValue({ scores: [] });
  const view = render(<Harness query="p" />);
  await tick();
  expect(api).not.toHaveBeenCalled();
  expect(screen.getByRole("status")).toHaveTextContent("Python Bytes");
  view.rerender(<Harness query="AI" />);
  await tick();
  expect(api).toHaveBeenCalledTimes(1);
});

test("current local matches appear before semantic refinement completes", async () => {
  const next = deferred();
  api
    .mockResolvedValueOnce({ scores: [{ id: "a", score: 1 }] })
    .mockReturnValueOnce(next.promise);
  const view = render(<Harness query="ocean" />);
  await tick();
  view.rerender(<Harness query="python" />);
  expect(screen.getByRole("status")).toHaveTextContent("Python Bytes");
  expect(screen.getByRole("status")).not.toHaveTextContent("Grand Blue");
  expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
  await tick();
  await act(async () => next.resolve({ scores: [{ id: "b", score: 1 }] }));
  expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "false");
});

test("large-library workers survive reading updates and are discarded after metadata changes or unmount", async () => {
  const workers = [];
  class SearchWorker {
    messages = [];
    terminate = vi.fn();
    constructor() {
      workers.push(this);
      this.handle = createSearchHandler((data) =>
        Promise.resolve().then(() => this.onmessage?.({ data })),
      );
    }
    postMessage(message) {
      this.messages.push(message);
      this.handle(message);
    }
  }
  vi.stubGlobal("Worker", SearchWorker);
  const rows = Array.from({ length: 50 }, (_, index) => ({
    ...items[index % 2],
    id: String(index),
    favorite: false,
  }));
  const view = render(<Harness query="scuba" rows={rows} mode="local" />);
  await act(async () => {});
  expect(screen.getByRole("status")).toHaveTextContent("Grand Blue");
  expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "false");
  expect(workers).toHaveLength(1);
  expect(workers[0].messages[0].items[0]).not.toHaveProperty("favorite");
  view.rerender(
    <Harness
      query="scuba"
      rows={rows.map((row) => ({ ...row, favorite: true, unread_count: 2 }))}
      mode="local"
    />,
  );
  await act(async () => {});
  expect(workers).toHaveLength(1);
  expect(workers[0].messages).toHaveLength(2);
  view.rerender(
    <Harness
      query="scuba"
      rows={rows.map((row) => ({ ...row, title: "Edited" }))}
      mode="local"
    />,
  );
  await act(async () => {});
  expect(workers).toHaveLength(2);
  expect(workers[0].terminate).toHaveBeenCalledTimes(1);
  view.unmount();
  expect(workers[1].terminate).toHaveBeenCalledTimes(1);
  expect(api).not.toHaveBeenCalled();
});

test("a stalled semantic request releases selection using current local results and ignores its late response", async () => {
  const stalled = deferred();
  api
    .mockResolvedValueOnce({ scores: [{ id: "a", score: 1 }] })
    .mockReturnValueOnce(stalled.promise);
  const view = render(<Harness query="ocean" />);
  await tick();
  view.rerender(<Harness query="unmatched words" />);
  await tick();
  expect(screen.getByRole("status")).toHaveTextContent("Grand Blue");
  await act(async () => {
    vi.advanceTimersByTime(8000);
  });
  expect(api.mock.calls[1][1].signal.aborted).toBe(true);
  expect(screen.getByRole("status")).not.toHaveTextContent("Grand Blue");
  expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "false");
  await act(async () => stalled.resolve({ scores: [{ id: "a", score: 1 }] }));
  expect(screen.getByRole("status")).not.toHaveTextContent("Grand Blue");
});
