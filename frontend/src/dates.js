const options = {
  day: { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" },
  checked: {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  },
  dateTime: {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  },
};
const formatters = new Map();

function format(value, kind, fallback) {
  if (!value) return fallback;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return fallback;
  let formatter = formatters.get(kind);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(undefined, options[kind]);
    formatters.set(kind, formatter);
  }
  return formatter.format(date);
}

export const day = (value) => format(value, "day", "Date unavailable");
export const checked = (value) => format(value, "checked", "Not checked");
export const dateTime = (value) =>
  format(value, "dateTime", "Date unavailable");
