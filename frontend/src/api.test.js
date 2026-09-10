import { post } from "./api";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

test.each(["8", "1"])(
  "API errors expose status and Retry-After %s",
  async (retry) => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 429,
      headers: new Headers({ "Retry-After": retry }),
      json: async () => ({ detail: "You can add one item every 8 seconds." }),
    });
    await expect(post("/items", { scan_id: "preview" })).rejects.toMatchObject({
      status: 429,
      retryAfter: Number(retry),
      message: "You can add one item every 8 seconds.",
    });
  },
);

test.each(["", "bad", "-1", "Infinity"])(
  "invalid Retry-After %s keeps a normal error",
  async (retry) => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 503,
      headers: new Headers({ "Retry-After": retry }),
      json: async () => ({ detail: "Database unavailable." }),
    });
    await expect(post("/items", { scan_id: "preview" })).rejects.toMatchObject({
      status: 503,
      message: "Database unavailable.",
    });
    await expect(
      post("/items", { scan_id: "preview" }),
    ).rejects.not.toHaveProperty("retryAfter");
  },
);
