import { expect, test } from "@playwright/test";

import { expectBanner, withApiDown } from "./helpers";

// T-CON-10 · CON-R7/R11: the shell (banner, navigation), the overview counts, and the error state.

test("overview shows the banner, navigation and counts from the console API", async ({ page }) => {
  await page.goto("/");
  await expectBanner(page);
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();

  const nav = page.getByRole("navigation", { name: "Console" });
  for (const name of ["Overview", "Customers", "Policies", "Test personas", "Audit trail"]) {
    await expect(nav.getByRole("link", { name })).toBeVisible();
  }
  await expect(nav.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  await expect(nav.getByText("Campaigns")).toBeVisible();
  await expect(nav.getByRole("link", { name: /Campaigns/ })).toHaveCount(0);

  // Values from tests/e2e/fixtures/overview.json (the persona-seeded fake bank behind the console API).
  await expect(page.getByTestId("stat-Test personas")).toContainText("12");
  await expect(page.getByTestId("stat-Policy documents")).toContainText("3");
  await expect(page.getByTestId("bucket-1-30")).toBeVisible();
});

test("an unreachable console API shows an error panel, not a stack trace", async ({ page }) => {
  await withApiDown(page, async () => {
    await page.goto("/");
    const panel = page.getByTestId("error-panel");
    await expect(panel).toContainText("Could not load data from the console API");
    await expect(panel).toContainText("The console API is not reachable at http://127.0.0.1:8599.");
    await expectBanner(page);
  });
  await expect(page.getByText(/at .*\.js:\d+/)).toHaveCount(0);
});

test("health endpoint and not-found page", async ({ page, request }) => {
  expect((await request.get("/healthz")).ok()).toBe(true);
  await page.goto("/no-such-page");
  await expect(page.getByTestId("not-found")).toBeVisible();
  await expectBanner(page);
});
