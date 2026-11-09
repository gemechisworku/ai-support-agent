// A fake console API for Playwright: replays tests/e2e/fixtures/*.json, which are recorded from the
// real console API (tests/console/web_fixtures.py). Usage: node tests/e2e/fake-console-api.mjs <port>
//
// Matching: method + path, then the fixture's `query` must be a subset of the request's query string
// (GET) or its `body` a subset of the JSON body (POST); the most specific fixture wins.
// Test controls: POST /__down makes every /api request fail at the socket (the API is "unreachable");
// POST /__up restores it. Requests without `Authorization: Bearer e2e-token` get 401.
import { readdirSync, readFileSync } from "node:fs";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TOKEN = "e2e-token";
const port = Number(process.argv[2] ?? 8599);
const dir = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures");
const fixtures = readdirSync(dir)
  .filter((f) => f.endsWith(".json"))
  .map((f) => JSON.parse(readFileSync(path.join(dir, f), "utf8")));

let down = false;

function isSubset(expected, actual) {
  return Object.entries(expected ?? {}).every(([k, v]) => actual?.[k] === v);
}

function match(method, pathname, query, body) {
  const candidates = fixtures.filter(
    (f) =>
      f.method === method &&
      f.path === pathname &&
      (method === "GET" ? isSubset(f.query, query) : isSubset(f.body, body)),
  );
  const weight = (f) => Object.keys((method === "GET" ? f.query : f.body) ?? {}).length;
  return candidates.sort((a, b) => weight(b) - weight(a))[0];
}

function send(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

const server = createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  let raw = "";
  req.on("data", (chunk) => (raw += chunk));
  req.on("end", () => {
    if (url.pathname === "/healthz") return send(res, 200, { status: "ok" });
    if (url.pathname === "/__down") return ((down = true), send(res, 200, { down }));
    if (url.pathname === "/__up") return ((down = false), send(res, 200, { down }));
    if (down) return req.socket.destroy();
    if (req.headers.authorization !== `Bearer ${TOKEN}`) {
      return send(res, 401, { detail: "missing or invalid console API token" });
    }
    const query = Object.fromEntries(url.searchParams);
    const body = raw ? JSON.parse(raw) : undefined;
    const fixture = match(req.method, decodeURIComponent(url.pathname), query, body);
    if (!fixture)
      return send(res, 404, { detail: `no fixture for ${req.method} ${url.pathname}${url.search}` });
    return send(res, fixture.status, fixture.response);
  });
});

server.listen(port, "127.0.0.1", () => console.log(`fake console API on http://127.0.0.1:${port}`));
