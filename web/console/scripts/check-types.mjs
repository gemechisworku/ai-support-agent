// Fails when src/lib/api/schema.ts is not what openapi-typescript generates from openapi.json
// (CON-R11). openapi.json itself is checked against the console API by the Python test suite.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const out = path.join(mkdtempSync(path.join(tmpdir(), "console-types-")), "schema.ts");
execFileSync("npx", ["openapi-typescript", "openapi.json", "--default-non-nullable", "false", "-o", out], {
  stdio: "pipe",
});
if (readFileSync(out, "utf8") !== readFileSync("src/lib/api/schema.ts", "utf8")) {
  console.error("src/lib/api/schema.ts is out of date: run `make console-types` (or `npm run types`).");
  process.exit(1);
}
console.log("src/lib/api/schema.ts matches openapi.json");
