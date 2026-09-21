import { vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import AddPage from "../Pages/AddPage";
import { post } from "../api";

vi.mock("../api", () => ({
  post: vi.fn(),
  examples: [],
  day: () => "Today",
}));
const wp = "https://example.wordpress.com/";
const detected = { source_method: "wordpress_com", note: "" };
const preview = {
  ...detected,
  scan_id: "preview",
  title: "Blog",
  url: wp,
  methods: [],
  warnings: [],
  entries: [{ url: wp + "post", title: "Post" }],
};
const change = (label, value) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const tick = async () => {
  await act(async () => vi.advanceTimersByTime(400));
};
const scan = async () => {
  await act(async () =>
    fireEvent.click(screen.getByRole("button", { name: "Scan links" })),
  );
};
function show(path = "/add") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <AddPage />
    </MemoryRouter>,
  );
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  post.mockImplementation(async (path) =>
    path === "/scans" ? preview : detected,
  );
});
afterEach(() => vi.useRealTimers());

test("Steam news URLs automatically select the news API", async () => {
  post.mockResolvedValue({ source_method: "steam", note: "" });
  show();
  change(/Source URL/, "https://store.steampowered.com/news/app/1623730");
  await tick();
  expect(screen.getByLabelText("Source method")).toHaveValue("steam");
  expect(screen.getByText(/official Steam announcements/)).toBeInTheDocument();
});

test("Enma watch URLs select the episode API and disclose access restrictions", async () => {
  post.mockResolvedValue({ source_method: "enma", note: "" });
  show();
  change(
    /Source URL/,
    "https://www.enma.lol/watch/grand-blue-dreaming-season-3-199111?ep=9",
  );
  await tick();
  expect(screen.getByLabelText("Source method")).toHaveValue("enma");
  expect(
    screen.getByText(/Enma may restrict server access/),
  ).toBeInTheDocument();
  expect(post).toHaveBeenCalledTimes(1);
});

test("Browser mode can be selected explicitly without API auto-detection", async () => {
  show();
  change(/Source URL/, "https://example.org/series");
  change("Source method", "browser");
  await tick();
  expect(post).not.toHaveBeenCalled();
  await scan();
  expect(post).toHaveBeenLastCalledWith(
    "/scans",
    expect.objectContaining({ source_method: "browser", detect_api: false }),
  );
  expect(screen.getByLabelText("Source method")).toHaveValue("browser");
});

test("typing is debounced, the API is selected, and scans persist the server choice", async () => {
  show();
  change(/Source URL/, "https://example.wordpress.co");
  change(/Source URL/, wp);
  expect(post).not.toHaveBeenCalled();
  await tick();
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith("/source-method/detect", { url: wp });
  expect(screen.getByLabelText("Source method")).toHaveValue("wordpress_com");
  expect(screen.getByText(/Selected from the URL/)).toBeInTheDocument();
  await scan();
  expect(post).toHaveBeenLastCalledWith(
    "/scans",
    expect.objectContaining({ source_method: "auto", detect_api: true }),
  );
  expect(
    screen.getByRole("button", { name: "Add to library" }),
  ).toBeInTheDocument();
  await tick();
  expect(post).toHaveBeenCalledTimes(2);
});

test("immediate Scan resolves the method without waiting for a detection response", async () => {
  show("/add?url=" + encodeURIComponent(wp));
  await scan();
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith(
    "/scans",
    expect.objectContaining({ url: wp, detect_api: true }),
  );
  expect(screen.getByLabelText("Source method")).toHaveValue("wordpress_com");
});

test("explicit Automatic opts out and stays selected as the URL changes", async () => {
  show();
  change(/Source URL/, wp);
  await tick();
  change("Source method", "auto");
  change(/Source URL/, "https://another.wordpress.com/");
  await tick();
  expect(post).toHaveBeenCalledTimes(1);
  await scan();
  expect(post).toHaveBeenLastCalledWith(
    "/scans",
    expect.objectContaining({ source_method: "auto", detect_api: false }),
  );
  expect(screen.getByLabelText("Source method")).toHaveValue("auto");
});

test("late detection cannot overwrite a manual choice", async () => {
  let resolve;
  post.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  show();
  change(/Source URL/, wp);
  await tick();
  change("Source method", "sitemap");
  await act(async () => resolve(detected));
  expect(screen.getByLabelText("Source method")).toHaveValue("sitemap");
  await scan();
  expect(post).toHaveBeenLastCalledWith(
    "/scans",
    expect.objectContaining({ source_method: "sitemap", detect_api: false }),
  );
});

test("changing URL clears the old API immediately and ignores out-of-order replies", async () => {
  let resolve;
  post.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  post.mockResolvedValue({ source_method: "auto", note: "" });
  show();
  change(/Source URL/, wp);
  await tick();
  change(/Source URL/, "https://example.org/");
  await tick();
  await act(async () => resolve(detected));
  expect(screen.getByLabelText("Source method")).toHaveValue("auto");
  expect(screen.queryByText(/Selected from the URL/)).not.toBeInTheDocument();
});

test("custom CSS keeps the page scanner", async () => {
  show();
  change(/Source URL/, wp);
  fireEvent.click(screen.getByText("Refine content detection"));
  change(/Content selector/, "article a");
  await tick();
  expect(post).not.toHaveBeenCalled();
  await scan();
  expect(post).toHaveBeenLastCalledWith(
    "/scans",
    expect.objectContaining({
      source_method: "auto",
      detect_api: false,
      selector: "article a",
    }),
  );
});

test("missing keys explain the fallback without an alert and failed detection does not block Scan", async () => {
  post.mockResolvedValueOnce({
    source_method: "auto",
    note: "YouTube detected. Server API key not configured.",
  });
  show();
  change(/Source URL/, "https://youtube.com/@alice");
  await tick();
  expect(screen.getByText(/Server API key not configured/)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  post.mockRejectedValueOnce(new Error("Network unavailable"));
  change(/Source URL/, wp);
  await tick();
  await scan();
  expect(screen.getByLabelText("Source method")).toHaveValue("wordpress_com");
  expect(
    screen.getByRole("button", { name: "Add to library" }),
  ).toBeInTheDocument();
});

test("arXiv searches select the metadata API without making source requests", async () => {
  post.mockResolvedValue({ source_method: "arxiv", note: "" });
  show();
  change(
    /Source URL/,
    "https://arxiv.org/search/cs?query=Hoffmann+et+al.+2022&searchtype=all",
  );
  await tick();
  expect(screen.getByLabelText("Source method")).toHaveValue("arxiv");
  expect(screen.getByText(/cached for one day/)).toBeInTheDocument();
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenLastCalledWith(
    "/source-method/detect",
    expect.anything(),
  );
});

test("advanced API previews explain skipped filters without blocking saving", async () => {
  const url =
    "https://arxiv.org/search/advanced?terms-0-term=Options+Pricing&terms-0-field=title&date-date_type=announced_date_first";
  const warning =
    "Skipped date filter: first-announced dates are not available through the arXiv API.";
  let scans = 0;
  post.mockImplementation(async (path) =>
    path === "/scans"
      ? {
          ...preview,
          source_method: "arxiv",
          scan_id: `advanced-${++scans}`,
          title: "Options Pricing",
          url,
          warnings: [warning],
          entries: [
            {
              title: "Option pricing with neural networks",
              url: "https://arxiv.org/abs/2609.00001",
            },
          ],
        }
      : { source_method: "arxiv", note: "" },
  );
  show();
  change(/Source URL/, url);
  await tick();
  expect(screen.getByLabelText("Source method")).toHaveValue("arxiv");
  expect(
    screen.getByText(
      /Unsupported filters are omitted and listed in the preview/,
    ),
  ).toBeInTheDocument();
  await scan();
  const message = screen.getByText(warning);
  expect(message.closest('[role="status"]')).toBeInTheDocument();
  expect(message.closest(".scan-results")).toContainElement(
    screen.getByRole("link", { name: /Option pricing with neural networks/ }),
  );
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add to library" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Dismiss message" }));
  expect(screen.queryByText(warning)).not.toBeInTheDocument();
  await scan();
  expect(screen.getByText(warning)).toBeInTheDocument();
  expect(post).toHaveBeenLastCalledWith(
    "/scans",
    expect.objectContaining({ url, detect_api: true }),
  );
});
