import { librarySearchIndex } from "./searchIndex.js";

export function createSearchHandler(send) {
  let search = null;
  return ({ type, id, items, query }) => {
    if (type === "index") {
      search = librarySearchIndex(items);
    } else if (type === "query" && search) {
      send({ id, scores: [...search(query)] });
    }
  };
}
