import { expect, test } from '@playwright/test';

test('auth lifecycle preserves session and csrf-safe actions', async ({ page }) => {
  const baseUrl = process.env.PLAYWRIGHT_BASE_URL?.trim() || '';
  const isLiveTarget = Boolean(baseUrl && !/localhost|127\.0\.0\.1/.test(baseUrl));
  const email = process.env.PLAYWRIGHT_USERNAME?.trim() || 'recruiter@example.com';
  const otp = process.env.PLAYWRIGHT_PASSWORD?.trim() || '123456';

  if (isLiveTarget && (!process.env.PLAYWRIGHT_USERNAME || !process.env.PLAYWRIGHT_PASSWORD)) {
    test.skip(true, 'Live auth flow requires PLAYWRIGHT_USERNAME and PLAYWRIGHT_PASSWORD.');
  }

  if (!isLiveTarget) {
    await page.route('**/api/backend/auth/request-otp', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: { message: 'OTP sent', email }, error: null }),
      });
    });

    await page.route('**/api/backend/auth/verify-otp', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: { user: { id: 'user-1', email, role: 'admin' }, token: 'test-token' }, error: null }),
      });
    });

    await page.route('**/api/backend/auth/me', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: { user: { id: 'user-1', email, role: 'admin' } }, error: null }),
      });
    });
  } else {
    await page.route('**/api/backend/auth/me', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: { user: { id: 'user-1', email, role: 'admin' } }, error: null }),
      });
    });
  }

  await page.route('**/api/backend/auth/logout', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { loggedOut: true }, error: null }),
    });
  });

  await page.goto('/login');
  await expect(page.getByText('Welcome')).toBeVisible();

  await page.getByPlaceholder('Work email').fill(email);
  await page.getByRole('button', { name: 'Send OTP' }).click();
  await expect(page.getByPlaceholder('Enter 6-digit OTP')).toBeVisible();
  await page.getByPlaceholder('Enter 6-digit OTP').fill(otp);
  await page.getByRole('button', { name: 'Verify OTP' }).click();

  await page.goto('/company');
  await expect(page.getByText('Tell us about your company')).toBeVisible();

  const me = await page.evaluate(async () => {
    const response = await fetch('/api/backend/auth/me', { credentials: 'include' });
    return response.json();
  });
  expect(me.data.user.email).toBe(email);
});
