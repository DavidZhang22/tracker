export async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(`/api${path}`, {
      credentials: "same-origin",
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
  } catch {
    throw new Error(
      "Cannot reach the tracker. Check your connection and retry. Changes have not been confirmed.",
    );
  }
  if (response.status === 401 && !path.startsWith("/auth/"))
    window.dispatchEvent(new Event("catchup:unauthorized"));
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(
      "The tracker server is unavailable. Please try again shortly.",
    );
  }
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "The request could not be completed.",
    );
  return data;
}
export const post = (path, body = {}) =>
  api(path, { method: "POST", body: JSON.stringify(body) });
export const patch = (path, body) =>
  api(path, { method: "PATCH", body: JSON.stringify(body) });
export const examples = [
  {
    title: "The Nebula’s Civilization",
    kind: "Comic",
    url: "https://asurascans.com/comics/the-nebulas-civilization-53fc8424",
  },
  {
    title: "ヨルシカ / n-buna Official",
    kind: "YouTube",
    url: "https://www.youtube.com/channel/UCRIgIJQWuBJ0Cv_VlU3USNA",
  },
  {
    title: "Mother of Learning",
    kind: "Novel",
    url: "https://www.royalroad.com/fiction/21220/mother-of-learning",
  },
  {
    title: "Climbing the Tower with Time-Stop Ability",
    kind: "Novel",
    url: "https://wetriedtls.com/series/climbing-the-tower-with-time-stop-ability",
  },
  {
    title: "Codeforces contests",
    kind: "Events",
    url: "https://codeforces.com/contests",
  },
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
