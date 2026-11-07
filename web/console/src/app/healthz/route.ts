// Liveness for compose and Playwright: the Next.js server is up (it does not call the console API).
export function GET() {
  return Response.json({ status: "ok" });
}
