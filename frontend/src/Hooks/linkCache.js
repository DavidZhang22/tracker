export const LINK_CACHE_BYTES = 1024 * 1024;

export function sameResult(left, right) {
  if (left === right) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object")
    return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const keys = Object.keys(left);
  return (
    keys.length === Object.keys(right).length &&
    keys.every(
      (key) =>
        Object.prototype.hasOwnProperty.call(right, key) &&
        sameResult(left[key], right[key]),
    )
  );
}

function retainedSize(value, remaining) {
  if (typeof value === "string") return value.length * 2 + 16;
  if (!value || typeof value !== "object") return 8;
  let size = 32;
  for (const key of Object.keys(value)) {
    size += key.length * 2 + 8 + retainedSize(value[key], remaining - size);
    if (size > remaining) break;
  }
  return size;
}

export function createLinkCache() {
  const entries = new Map();
  let bytes = 0;
  return {
    get(path) {
      return entries.get(path);
    },
    set(path, data, time) {
      const old = entries.get(path);
      if (old) {
        bytes -= old.bytes;
        entries.delete(path);
      }
      const weight = retainedSize(data, LINK_CACHE_BYTES);
      if (weight > LINK_CACHE_BYTES) return;
      while (entries.size >= 8 || bytes + weight > LINK_CACHE_BYTES) {
        const oldest = entries.keys().next().value;
        bytes -= entries.get(oldest).bytes;
        entries.delete(oldest);
      }
      entries.set(path, { data, time, bytes: weight });
      bytes += weight;
    },
    clear() {
      entries.clear();
      bytes = 0;
    },
  };
}
