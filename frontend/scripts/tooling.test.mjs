import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";
import { fileURLToPath, URL } from "node:url";
import { createServer } from "vite";
import config from "../vite.config.js";

const frontend = fileURLToPath(new URL("../", import.meta.url));
const backendReadme = fileURLToPath(
  new URL("../../backend/README.MD", import.meta.url),
).replaceAll("\\", "/");

function request(origin, path, options = {}) {
  return new Promise((resolve, reject) => {
    const outgoing = http.request(new URL(path, origin), {
      agent: false,
      ...options,
    });
    outgoing.on("response", resolve);
    outgoing.on("error", reject);
    outgoing.setTimeout(5000, () => {
      outgoing.destroy(new Error(`Timed out requesting ${path}`));
    });
    outgoing.end();
  });
}

async function body(response) {
  let text = "";
  for await (const chunk of response) text += chunk.toString();
  return text;
}

test(
  "Vite serves the SPA and safely proxies incremental API responses",
  {
    timeout: 30000,
  },
  async (t) => {
    assert.equal(config.server.host, "127.0.0.1");
    assert.equal(config.server.port, 3000);
    assert.equal(config.server.strictPort, true);
    assert.equal(config.server.proxy["/api"].target, "http://127.0.0.1:8000");
    assert.equal(config.server.proxy["/api"].proxyTimeout, 240000);

    const releaseSecond = Promise.withResolvers();
    const forwarded = Promise.withResolvers();
    const firstLine = '{"phase":"started"}\n';
    const secondLine = '{"phase":"complete"}\n';
    const api = http.createServer(async (incoming, response) => {
      incoming.resume();
      forwarded.resolve({
        method: incoming.method,
        path: incoming.url,
        host: incoming.headers.host,
        origin: incoming.headers.origin,
      });
      response.writeHead(200, { "Content-Type": "application/x-ndjson" });
      response.write(firstLine);
      await releaseSecond.promise;
      response.end(secondLine);
    });
    let vite;
    try {
      await new Promise((resolve, reject) => {
        api.once("error", reject);
        api.listen(0, "127.0.0.1", resolve);
      });
      const apiOrigin = `http://127.0.0.1:${api.address().port}`;
      vite = await createServer({
        ...config,
        configFile: false,
        root: frontend,
        logLevel: "silent",
        server: {
          ...config.server,
          port: 0,
          proxy: {
            "/api": { ...config.server.proxy["/api"], target: apiOrigin },
          },
        },
      });
      await vite.listen();
      const origin = `http://127.0.0.1:${vite.httpServer.address().port}`;

      await t.test(
        "forwards the API path and Origin without buffering NDJSON",
        async () => {
          const path = "/api/tooling/stream?scope=fixture";
          const response = await request(origin, path, {
            method: "POST",
            headers: { Origin: origin },
          });
          assert.equal(response.statusCode, 200);
          assert.match(
            response.headers["content-type"],
            /application\/x-ndjson/,
          );
          assert.deepEqual(await forwarded.promise, {
            method: "POST",
            path,
            host: new URL(apiOrigin).host,
            origin,
          });

          // The API cannot finish until the client has received its first line.
          const chunks = response[Symbol.asyncIterator]();
          let first = "";
          while (!first.includes("\n")) {
            const next = await chunks.next();
            assert.equal(
              next.done,
              false,
              "The stream ended before its first line",
            );
            first += next.value.toString();
          }
          assert.equal(first, firstLine);
          releaseSecond.resolve();
          assert.equal(await body(chunks), secondLine);
        },
      );

      await t.test(
        "serves the HTML entry point for a nested SPA route",
        async () => {
          const response = await request(origin, "/items/tooling-fixture", {
            headers: { Accept: "text/html" },
          });
          assert.equal(response.statusCode, 200);
          assert.match(response.headers["content-type"], /text\/html/);
          const html = await body(response);
          assert.match(html, /<div id="root"><\/div>/);
          assert.match(html, /src="\/src\/index\.jsx"/);
        },
      );

      await t.test("rejects an untrusted Host header", async () => {
        const response = await request(origin, "/", {
          headers: { Host: "untrusted.example" },
        });
        assert.equal(response.statusCode, 403);
        assert.match(await body(response), /Blocked request/);
      });

      await t.test(
        "blocks direct filesystem access outside the frontend",
        async () => {
          const response = await request(origin, `/@fs/${backendReadme}`);
          assert.equal(response.statusCode, 403);
          assert.match(
            await body(response),
            /outside of Vite serving allow list/,
          );
        },
      );
    } finally {
      releaseSecond.resolve();
      api.closeAllConnections();
      await Promise.all([
        vite?.close(),
        new Promise((resolve) => api.close(resolve)),
      ]);
    }
  },
);
