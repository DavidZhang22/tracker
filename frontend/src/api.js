async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`/api${path}`, {
      credentials: "same-origin",
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error(
      "Cannot reach the tracker. Check your connection and retry. Changes have not been confirmed.",
    );
  }
  if (response.status === 401 && !path.startsWith("/auth/"))
    window.dispatchEvent(new Event("trackify:unauthorized"));
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(
      typeof data.detail === "string"
        ? data.detail
        : "The request could not be completed.",
    );
    error.status = response.status;
    const retryAfter = Number(response.headers.get("Retry-After"));
    if (Number.isFinite(retryAfter) && retryAfter > 0)
      error.retryAfter = Math.ceil(retryAfter);
    throw error;
  }
  return response;
}
export async function api(path, options = {}) {
  const response = await request(path, options);
  try {
    return await response.json();
  } catch {
    throw new Error(
      "The tracker server is unavailable. Please try again shortly.",
    );
  }
}

export async function refreshLibrary(onEvent, signal, deep = false) {
  const response = await request(`/refresh?stream=true&deep=${deep}`, {
    method: "POST",
    body: "{}",
    signal,
    headers: { Accept: "application/x-ndjson" },
  });
  if (!response.body?.getReader)
    throw new Error(
      "Live refresh is unavailable in this browser. Refresh items individually.",
    );
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "",
    complete = false;
  try {
    while (true) {
      const { value, done } = await reader.read().catch((error) => {
        if (error.name === "AbortError") throw error;
        throw new Error(
          "Refresh connection closed. Completed updates were kept. Please try again.",
        );
      });
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        let event;
        try {
          if (line.length > 262144) throw new Error();
          event = JSON.parse(line);
        } catch {
          throw new Error(
            "Refresh returned an unreadable update. Completed updates were kept.",
          );
        }
        if (event.type === "error") throw new Error(event.detail);
        if (event.type !== "heartbeat") onEvent(event);
        if (event.type === "complete") complete = true;
      }
      if (buffer.length > 262144)
        throw new Error("Refresh returned an unreadable update.");
      if (complete) return;
      if (done)
        throw new Error(
          "Refresh connection closed. Completed updates were kept. Please try again.",
        );
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
export const post = (path, body = {}) =>
  api(path, { method: "POST", body: JSON.stringify(body) });
export const patch = (path, body) =>
  api(path, { method: "PATCH", body: JSON.stringify(body) });
export const uploadFile = (file, options = {}) =>
  api(
    `/scans/import?${new URLSearchParams({ filename: file.name, ...options })}`,
    {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    },
  );
export const examples = [
  { title: "xkcd", kind: "Comic archive", url: "https://xkcd.com/archive/" },
];
export const day = (value) =>
  value
    ? new Date(value).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      })
    : "Date unavailable";
export const checked = (value) =>
  value
    ? new Date(value).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "Not checked";
