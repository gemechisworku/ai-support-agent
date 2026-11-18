import { expect, type Page } from "@playwright/test";

export const FAKE_API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? 8599}`;
export const BANNER = "Synthetic data · local POC — every customer, loan and policy here is fake.";

/** Make the fake console API unreachable (socket closed) for the duration of `fn`. */
export async function withApiDown(page: Page, fn: () => Promise<void>): Promise<void> {
  await page.request.post(`${FAKE_API}/__down`);
  try {
    await fn();
  } finally {
    await page.request.post(`${FAKE_API}/__up`);
  }
}

/** Every console page shows the synthetic-data banner (CON-R7). */
export async function expectBanner(page: Page): Promise<void> {
  await expect(page.getByTestId("synthetic-banner")).toHaveText(BANNER);
}
