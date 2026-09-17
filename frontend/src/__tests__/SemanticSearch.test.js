import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import useLibrarySearch from "../useLibrarySearch";
import Description from "../Components/Description";
import { librarySearchIndex } from "../media";
import { api } from "../api";

jest.mock("../api", () => ({ api: jest.fn() }));
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
function Harness({ query, rows = items, trash = false }) {
  const scores = useLibrarySearch(rows, query, trash);
  return (
    <output>
      {rows
        .filter((row) => scores.get(row.id) > 0)
        .map((row) => row.title)
        .join(", ")}
    </output>
  );
}
beforeEach(() => {
  jest.useFakeTimers();
  api.mockReset();
});
afterEach(() => {
  jest.clearAllTimers();
  jest.useRealTimers();
});
const tick = async () =>
  act(async () => {
    jest.advanceTimersByTime(251);
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
  expect(librarySearchIndex(items)("pyhton bytes").get("b")).toBeGreaterThan(0);
  expect(librarySearchIndex(items)("scuba diving").get("a")).toBeGreaterThan(0);
});

test("descriptions are plain text with an accessible edit link", () => {
  const text = '<img src=x onerror="alert(1)"> A source description.';
  const view = render(
    <MemoryRouter>
      <Description item={{ ...items[0], description: text }} />
    </MemoryRouter>,
  );
  expect(screen.getByText(text)).toBeVisible();
  expect(view.container.querySelector("img")).toBeNull();
  expect(screen.getByRole("link", { name: "Edit" })).toHaveAttribute(
    "href",
    "/settings?item=a#item-settings",
  );
});
