import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import SourceStatus from "../Components/SourceStatus";

const status = {
  host: "news.example.org",
  checked_at: "2026-09-24T10:00:00Z",
  status: "access_blocked",
  message: "This site's listing refused the last automated check.",
  alternatives: [
    {
      url: "https://feeds.example.org/top.xml?a=b&c=d",
      label: "Publisher headlines",
      relationship: "official",
    },
  ],
};

test("offers an explicit preview without opening or fetching the alternative", () => {
  const fetch = vi.spyOn(globalThis, "fetch");
  render(
    <MemoryRouter>
      <SourceStatus status={status} />
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("link", { name: "Publisher headlines" }),
  ).toHaveAttribute(
    "href",
    `/add?url=${encodeURIComponent(status.alternatives[0].url)}`,
  );
  expect(screen.getByText(/Coverage may differ/)).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Dismiss message" }));
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  fetch.mockRestore();
});

test("does not render unsafe or unverified destinations", () => {
  render(
    <MemoryRouter>
      <SourceStatus
        status={{
          ...status,
          alternatives: [
            {
              url: "javascript:alert(1)",
              label: "Unsafe",
              relationship: "official",
            },
            {
              url: "https://name:password@example.org",
              label: "Credentials",
              relationship: "official",
            },
            {
              url: "https://example.org",
              label: "Unverified",
              relationship: "unknown",
            },
          ],
        }}
      />
    </MemoryRouter>,
  );
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});

test("identifies stale checks and can show a newer observation after dismissal", () => {
  const { rerender } = render(
    <MemoryRouter>
      <SourceStatus status={{ ...status, stale: true }} />
    </MemoryRouter>,
  );
  expect(screen.getByText(/Availability may have changed/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Dismiss message" }));
  rerender(
    <MemoryRouter>
      <SourceStatus
        status={{ ...status, checked_at: "2026-09-25T10:00:00Z" }}
      />
    </MemoryRouter>,
  );
  expect(screen.getByRole("status")).toBeInTheDocument();
});
