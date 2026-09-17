import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cwd } from "node:process";
import { describe, expect, test } from "vitest";

const cases = JSON.parse(
  readFileSync(
    resolve(cwd(), "../backend/tests/fixtures/pagination-controls.json"),
    "utf8",
  ),
);
const worker = readFileSync(
  resolve(cwd(), "../deploy/browser/worker.py"),
  "utf8",
);
const control = worker.match(/CONTROL = r"""([\s\S]*?)"""/)[1];
const select = new Function("document", `return (${control})();`);

describe("browser pagination matches the parser's prose-expansion rules", () => {
  test.each(cases)("$name", ({ html, pagination }) => {
    const doc = document.implementation.createHTMLDocument();
    doc.body.innerHTML = html;
    for (const element of doc.querySelectorAll("*")) {
      element.checkVisibility = () => true;
    }
    expect(Boolean(select(doc))).toBe(pagination);
  });
});
