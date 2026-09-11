import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SuggestionsPage from "./Pages/SuggestionsPage";
import { api, patch, post } from "./api";

jest.mock("./api", () => ({
  api: jest.fn(),
  patch: jest.fn(),
  post: jest.fn(),
}));
const suggestion = {
  id: "rec1",
  url: "https://fiction.example/series/witch",
  title: "Forest Witch",
  summary: "Magic in the forest",
  kind: "novel",
  source_id: "source1",
  source_title: "Magic School",
  reason: "From a favorite source",
};
beforeEach(() => jest.resetAllMocks());
function page() {
  render(
    <MemoryRouter>
      <SuggestionsPage />
    </MemoryRouter>,
  );
}
async function click(button) {
  await act(async () => fireEvent.click(button));
}

test("suggestions show observed provenance and enter review before adding", async () => {
  api.mockResolvedValue({ suggestions: [suggestion] });
  page();
  const source = await screen.findByRole("link", { name: /Forest Witch/ });
  expect(source).toHaveAttribute("href", suggestion.url);
  expect(screen.getByRole("link", { name: "Magic School" })).toHaveAttribute(
    "href",
    "/items/source1",
  );
  expect(screen.getByRole("link", { name: "Review & add" })).toHaveAttribute(
    "href",
    `/add?url=${encodeURIComponent(suggestion.url)}`,
  );
  expect(post).not.toHaveBeenCalled();
});

test("suggestion dismissal persists through API and can be undone", async () => {
  api.mockResolvedValue({ suggestions: [suggestion] });
  patch.mockResolvedValue({ updated: true });
  page();
  await click(
    await screen.findByRole("button", {
      name: "Not interested in Forest Witch",
    }),
  );
  expect(patch).toHaveBeenCalledWith("/suggestions/rec1", { dismissed: true });
  expect(
    screen.queryByRole("link", { name: /Forest Witch/ }),
  ).not.toBeInTheDocument();
  await click(screen.getByRole("button", { name: "Undo" }));
  expect(patch).toHaveBeenLastCalledWith("/suggestions/rec1", {
    dismissed: false,
  });
  expect(
    screen.getByRole("link", { name: /Forest Witch/ }),
  ).toBeInTheDocument();
});

test("failed dismissal keeps the recommendation and has a dismissible error", async () => {
  api.mockResolvedValue({ suggestions: [suggestion] });
  patch.mockRejectedValue(new Error("Storage unavailable"));
  page();
  await click(
    await screen.findByRole("button", {
      name: "Not interested in Forest Witch",
    }),
  );
  expect(
    screen.getByRole("link", { name: /Forest Witch/ }),
  ).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Storage unavailable");
  await click(screen.getByRole("button", { name: "Dismiss error" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("update suggestions only requests the cache backfill endpoint", async () => {
  api.mockResolvedValue({ suggestions: [] });
  post.mockResolvedValue({ suggestions: [suggestion], pages_used: 1 });
  page();
  await screen.findByText("No suggestions yet");
  await click(screen.getByRole("button", { name: "Update suggestions" }));
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith("/suggestions/rebuild");
  expect(screen.getByRole("status")).toHaveTextContent(
    "No extra web requests.",
  );
  expect(
    screen.getByRole("link", { name: /Forest Witch/ }),
  ).toBeInTheDocument();
});
