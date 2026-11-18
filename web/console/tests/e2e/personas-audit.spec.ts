import { expect, test } from "@playwright/test";

import { expectBanner } from "./helpers";

// T-CON-13 · CON-R10/R7: persona test sheet and audit trail, against fixtures recorded from the console API.

const DEMO_CALL = "fbde0dde-804f-4a79-ba80-2795d29b3079";
const TEST_IDENTITY = /Synthetic test identity/i;

test("persona sheet lists 12 personas with labelled synthetic test identities", async ({ page }) => {
  await page.goto("/personas");
  await expectBanner(page);
  await expect(page.getByRole("heading", { level: 1, name: "Test personas" })).toBeVisible();
  await expect(page.getByTestId("persona-list").locator("[data-testid^='persona-9']")).toHaveCount(12);

  const frank = page.getByTestId("persona-9002");
  await expect(frank.getByRole("link", { name: "Frank Fischer" })).toHaveAttribute("href", "/customers/9002");
  await expect(frank).toContainText("ext. 302");
  const identity = frank.getByRole("region", { name: /Synthetic test identity: Frank Fischer/ });
  await expect(identity).toContainText(TEST_IDENTITY);
  await expect(identity.getByTestId("persona-dob")).toHaveText("14 Mar 1985");
  await expect(identity).toContainText("4417");
  await expect(page.getByTestId("persona-dob")).toHaveCount(12);
  await expect(page.getByTestId("persona-9007")).toContainText("8842");
});

test("audit trail shows the demo call's 5 tool calls in order with the denial highlighted", async ({
  page,
}) => {
  await page.goto("/audit");
  await expectBanner(page);
  await expect(page.getByTestId("audit-hint")).toBeVisible();

  await page.getByLabel("Call id").fill(DEMO_CALL);
  await page.getByRole("button", { name: "Show audit trail" }).click();
  await expect(page).toHaveURL(new RegExp(`/audit\\?call_id=${DEMO_CALL}$`));

  const rows = page.getByRole("rowgroup").nth(1).getByRole("row");
  await expect(rows).toHaveCount(5);
  const tools = await rows.evaluateAll((trs) => trs.map((tr) => tr.querySelectorAll("td")[1]?.textContent));
  expect(tools).toEqual([
    "verify_identity",
    "get_loan_status",
    "search_policy",
    "record_promise_to_pay",
    "record_call_outcome",
  ]);
  await expect(rows.nth(3)).toHaveAttribute("data-tone", "danger");
  await expect(rows.nth(3)).toContainText("deny");
  await expect(page.locator("tr[data-tone='danger']")).toHaveCount(1);
});

test("an unknown call shows an empty state", async ({ page }) => {
  await page.goto("/audit?call_id=no-such-call");
  await expect(page.getByText("No gateway tool calls recorded for call no-such-call.")).toBeVisible();
  await expect(page.getByLabel("Call id")).toHaveValue("no-such-call");
});

test("a DOB appears only on the persona sheet", async ({ page }) => {
  for (const path of ["/", "/audit", `/audit?call_id=${DEMO_CALL}`]) {
    await page.goto(path);
    await expectBanner(page);
    await expect(page.getByText(/date of birth/i)).toHaveCount(0);
    await expect(page.getByText(TEST_IDENTITY)).toHaveCount(0);
    await expect(page.getByText("14 Mar 1985")).toHaveCount(0);
  }
  await page.goto("/personas");
  await expect(page.getByText(/date of birth/i).first()).toBeVisible();
});
