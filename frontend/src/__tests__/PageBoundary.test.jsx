import { fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import PageBoundary from "../Components/PageBoundary";

function FailedPage() {
  throw new Error("Failed to fetch dynamically imported module");
}

test("a missing page chunk leaves navigation and a reload option available", () => {
  const errors = vi.spyOn(console, "error").mockImplementation(() => {});
  const expectedFailure = (event) => {
    if (event.error?.message === "Failed to fetch dynamically imported module")
      event.preventDefault();
  };
  window.addEventListener("error", expectedFailure);
  try {
    render(
      <MemoryRouter initialEntries={["/unavailable"]}>
        <Link to="/">Library</Link>
        <PageBoundary embedded>
          <Routes>
            <Route path="/unavailable" element={<FailedPage />} />
            <Route path="/" element={<h1>My library</h1>} />
          </Routes>
        </PageBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Page unavailable");
    expect(screen.getByRole("button", { name: "Reload page" })).toBeEnabled();
    fireEvent.click(screen.getByRole("link", { name: "Library" }));
    expect(
      screen.getByRole("heading", { name: "My library" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  } finally {
    window.removeEventListener("error", expectedFailure);
    errors.mockRestore();
  }
});
