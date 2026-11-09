// Run the production `standalone` server exactly as docker/console.Dockerfile ships it: copy the
// static assets next to server.js, then start it. Used by Playwright (e2e) and `npm run start`.
//   node scripts/start-standalone.mjs [port] [host]
import { spawn } from "node:child_process";
import { cpSync, existsSync } from "node:fs";

const port = process.argv[2] ?? process.env.PORT ?? "8501";
const host = process.argv[3] ?? process.env.HOSTNAME ?? "127.0.0.1";

if (!existsSync(".next/standalone/server.js")) {
  console.error("No standalone build: run `npm run build` first.");
  process.exit(1);
}
cpSync(".next/static", ".next/standalone/.next/static", { recursive: true });
if (existsSync("public")) cpSync("public", ".next/standalone/public", { recursive: true });

const server = spawn(process.execPath, ["server.js"], {
  cwd: ".next/standalone",
  stdio: "inherit",
  env: { ...process.env, PORT: port, HOSTNAME: host },
});
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.kill(signal));
server.on("exit", (code) => process.exit(code ?? 0));
