import { expect, test } from "@playwright/test";

import { expectBanner } from "./helpers";

// T-CON-12 · CON-R9: policy list, document chunks, search playground (fixtures: tests/e2e/fixtures).

test("the policy list shows every document with versions and links", async ({ page }) => {
  await page.goto("/policies");
  await expectBanner(page);
  await expect(page.getByTestId("document-count")).toHaveText("3 documents");
  const table = page.getByRole("table", { name: "Policy documents" });
  await expect(table.getByRole("link")).toHaveCount(3);
  await expect(table).toContainText("1.1 (1 Jan 2027) · 1.0 (1 Jan 2026)");
  await expect(table).toContainText("internal");

  await table.getByRole("link", { name: "Grace Periods, Late Fees and Penalty Interest" }).click();
  await expect(page).toHaveURL(/\/policies\/late-fees-penalty-interest$/);
  await expect(page.getByTestId("document-chunk")).toHaveCount(2);
});

test("a document shows its chunks with section paths and a version switcher", async ({ page }) => {
  await page.goto("/policies/late-fees-penalty-interest?version=1.0");
  await expectBanner(page);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Grace Periods, Late Fees and Penalty Interest",
  );
  await expect(page.getByTestId("document-version")).toHaveText("late-fees-penalty-interest@1.0");
  const chunks = page.getByTestId("document-chunk");
  await expect(chunks).toHaveCount(2);
  await expect(chunks.nth(0)).toContainText("Grace Periods > Grace periods by product");
  await expect(chunks.nth(1)).toContainText("| Personal Loan | $25 flat |");
  const versions = page.getByRole("navigation", { name: "Versions" });
  await expect(versions.getByRole("link")).toHaveCount(2);
  await expect(versions.getByRole("link", { name: /1\.0/ })).toHaveAttribute("aria-current", "page");
});

test("a confident search shows cited chunks", async ({ page }) => {
  await page.goto("/policies");
  await page.getByRole("link", { name: "Open the search playground" }).click();
  await page.getByRole("textbox", { name: "Question" }).fill("late fee personal loan");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page).toHaveURL(/q=late\+fee\+personal\+loan/);
  await expect(page.getByTestId("confidence")).toHaveAttribute("data-confident", "true");
  await expect(page.getByTestId("citation")).toHaveText("fees-schedule@1.0#Schedule of Loan Fees");
  await expect(page.getByTestId("search-chunk")).toContainText("similarity 0.710");
});

test("an off-topic search is not confident and says the agent would follow up", async ({ page }) => {
  await page.goto("/policies/search?q=capital+of+France");
  await expectBanner(page);
  const note = page.getByTestId("confidence");
  await expect(note).toHaveAttribute("data-confident", "false");
  await expect(note).toContainText("the agent would offer a follow-up instead of answering");
  await expect(page.getByTestId("search-chunk")).toHaveCount(0);
});

test("a failing document shows an error panel and the search still works", async ({ page }) => {
  await page.goto("/policies/fees-schedule?version=9.9");
  await expect(page.getByTestId("error-panel")).toContainText("Could not load this policy document");
  await expect(page.getByTestId("error-panel")).toContainText("HTTP 500");
  await expectBanner(page);

  await page.goto("/policies/search?q=late+fee+personal+loan");
  await expect(page.getByTestId("confidence")).toHaveAttribute("data-confident", "true");
});

test("an unknown document is not found", async ({ page }) => {
  await page.goto("/policies/no-such-doc");
  await expect(page.getByTestId("not-found")).toBeVisible();
  await expectBanner(page);
});
