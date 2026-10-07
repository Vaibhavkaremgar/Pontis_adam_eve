import { expect, test } from "@playwright/test";

async function installSession(page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "pontis_user",
      JSON.stringify({ id: "user-1", email: "recruiter@example.com", role: "AGENCY_USER" })
    );
  });
  await page.route("**/api/backend/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        data: { user: { id: "user-1", email: "recruiter@example.com", role: "AGENCY_USER" } },
        error: null,
      }),
    })
  );
}

for (const option of [
  { testId: "opportunity-type-jobs", value: "jobs" },
  { testId: "opportunity-type-intern", value: "intern" },
]) {
  test(`${option.value} selection persists into the company step`, async ({ page }) => {
    await installSession(page);
    await page.goto("/opportunity-type");
    await page.getByTestId(option.testId).click();
    await expect(page).toHaveURL(/\/company$/);
    await expect.poll(() =>
      page.evaluate(() => JSON.parse(window.sessionStorage.getItem("pontis_job") || "{}").opportunityType)
    ).toBe(option.value);
  });
}
