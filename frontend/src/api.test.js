import { post, refreshLibrary } from "./api";
import { TextDecoder, TextEncoder } from "util";

const originalFetch = global.fetch;
const originalDecoder = global.TextDecoder;
afterEach(() => {
  global.fetch = originalFetch;
  global.TextDecoder = originalDecoder;
});

function streamingReader(chunks) {
  global.TextDecoder = TextDecoder;
  const reader = {
    read: jest.fn(),
    cancel: jest.fn().mockResolvedValue(),
    releaseLock: jest.fn(),
  };
  chunks.forEach((value) =>
    reader.read.mockResolvedValueOnce({ value, done: false }),
  );
  reader.read.mockResolvedValue({ done: true });
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    body: { getReader: () => reader },
  });
  return reader;
}

test("stream parser handles split UTF-8 and multiple updates per chunk", async () => {
  const events = [
    { type: "item", item: { title: "魔法" } },
    { type: "complete", checked: 1 },
  ];
  const wireEvents = [{ type: "heartbeat" }, ...events];
  const bytes = new TextEncoder().encode(
    wireEvents.map((e) => JSON.stringify(e)).join("\n") + "\n",
  );
  const chunks = Array.from(bytes, (byte) => new Uint8Array([byte]));
  const reader = streamingReader(chunks),
    receive = jest.fn();
  await refreshLibrary(receive);
  expect(receive.mock.calls.map(([event]) => event)).toEqual(events);
  expect(reader.cancel).toHaveBeenCalled();
  expect(reader.releaseLock).toHaveBeenCalled();
});

test("incomplete stream reports interruption after delivering saved item updates", async () => {
  const event = { type: "item", item: { id: "done" } };
  streamingReader([new TextEncoder().encode(JSON.stringify(event) + "\n")]);
  const receive = jest.fn();
  await expect(refreshLibrary(receive)).rejects.toThrow(
    "Completed updates were kept",
  );
  expect(receive).toHaveBeenCalledWith(event);
});

test("storage errors inside a stream keep the server's actionable message", async () => {
  streamingReader([
    new TextEncoder().encode(
      '{"type":"error","detail":"Storage unavailable. Completed updates were kept."}\n',
    ),
  ]);
  await expect(refreshLibrary(jest.fn())).rejects.toThrow(
    "Storage unavailable",
  );
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
