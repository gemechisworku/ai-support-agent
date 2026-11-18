import { expect, test } from "@playwright/test";

import { expectBanner } from "./helpers";

// T-CON-11 · CON-R8/R7: customer search and the customer page, against fixtures recorded from the
// console API over the persona-seeded fake bank (tests/e2e/fixtures/customer*.json).

test("search Frank, open him, and check the CON-R8 values in every tab", async ({ page }) => {
  await page.goto("/customers");
  await expectBanner(page);
  await page.getByRole("textbox", { name: "Search" }).fill("frank");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page).toHaveURL(/\/customers\?q=frank/);
  await expect(page.getByTestId("result-count")).toHaveText("1 customer · page 1 of 1");
  const row = page.getByRole("row", { name: /Frank Fischer/ });
  await expect(row).toContainText("•••• 0302");
  await row.getByRole("link", { name: "Frank Fischer" }).click();

  await expect(page).toHaveURL(/\/customers\/9002$/);
  await expectBanner(page);
  await expect(page.getByRole("heading", { level: 1, name: "Frank Fischer" })).toBeVisible();
  await expect(page.getByTestId("loan-headline")).toHaveText(
    "•••• 4417 · Personal Loan · 5 DPD · $200.72 overdue since 4 November 2026",
  );
  // Profile (default tab): contact preferences, masked phone, no DOB anywhere.
  await expect(page.getByTestId("contact-preferences")).toContainText("Consent to AI voice calls");
  await expect(page.getByText("frank.fischer@example.com")).toBeVisible();
  await expect(page.getByText(/date of birth|DOB/i)).toHaveCount(0);

  const tabs = page.getByRole("navigation", { name: "Customer sections" });
  await tabs.getByRole("link", { name: "Loans" }).click();
  const loanRow = page.getByTestId("tab-loans").getByRole("row", { name: /•••• 4417/ });
  await expect(loanRow).toContainText("$200.72");
  await expect(loanRow).toContainText("4 Nov 2026");
  await expect(loanRow).toContainText("1-30");
  await expect(loanRow.getByRole("cell").nth(6)).toHaveText("5");

  await tabs.getByRole("link", { name: "Schedule" }).click();
  const missed = page.getByTestId("tab-schedule").getByRole("row", { name: /4 Nov 2026/ });
  await expect(missed).toContainText("missed");
  await expect(missed).toHaveAttribute("data-tone", "danger");

  await tabs.getByRole("link", { name: "Transactions" }).click();
  await expect(page.getByTestId("tab-transactions")).toContainText("PMT-••••4417-017");
  // References are masked: no run of 5+ digits in any reference cell.
  const references = await page.getByTestId("tab-transactions").locator("code").allTextContents();
  expect(references.length).toBeGreaterThan(0);
  expect(references.filter((r) => /\d{5,}/.test(r))).toEqual([]);

  await tabs.getByRole("link", { name: "History" }).click();
  await expect(page.getByTestId("tab-history")).toContainText("No calls yet.");
});

test("filters and the selected tab survive a reload", async ({ page }) => {
  await page.goto("/customers?q=frank&segment=retail&bucket=1-30");
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Search" })).toHaveValue("frank");
  await expect(page.getByRole("combobox", { name: "Segment" })).toHaveValue("retail");
  await expect(page.getByRole("combobox", { name: "Bucket" })).toHaveValue("1-30");

  await page.goto("/customers/9002?tab=schedule");
  await page.reload();
  await expect(page.getByTestId("tab-schedule")).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Customer sections" }).getByRole("link", { name: "Schedule" }),
  ).toHaveAttribute("aria-current", "page");
});

test("no matches and unknown customers", async ({ page }) => {
  await page.goto("/customers?q=zz-nobody");
  await expect(page.getByText("No customers match.")).toBeVisible();

  await page.goto("/customers/424242");
  await expect(page.getByTestId("not-found")).toBeVisible();
  await expectBanner(page);

  await page.goto("/customers/not-a-number");
  await expect(page.getByTestId("not-found")).toBeVisible();
});
