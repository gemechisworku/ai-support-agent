// CON-R11/R7: no console API token, env var name or upstream URL may reach the browser bundle.
// Scans the client assets that `next build` wrote to .next/static.
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

const FORBIDDEN = [
  "CONSOLE_API_TOKEN",
  "CONSOLE_API_URL",
  "console-api:8004",
  "localhost:8004",
  "/api/customers", // console API paths are called only from the server
  ...(process.env.CONSOLE_API_TOKEN ? [process.env.CONSOLE_API_TOKEN] : []),
];

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) yield* files(p);
    else yield p;
  }
}

const hits = [];
for (const file of files(".next/static")) {
  const text = readFileSync(file, "utf8");
  for (const needle of FORBIDDEN) if (text.includes(needle)) hits.push(`${file}: ${needle}`);
}
if (hits.length) {
  console.error("Server-only values found in the client bundle:\n" + hits.join("\n"));
  process.exit(1);
}
console.log("client bundle is clean");
