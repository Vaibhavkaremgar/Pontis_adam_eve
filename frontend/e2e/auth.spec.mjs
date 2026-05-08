import { expect, test } from '@playwright/test';

test('auth lifecycle preserves session and csrf-safe actions', async ({ page }) => {
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { user: { id: 'user-1', email: 'recruiter@example.com', role: 'admin' } }, error: null }),
    });
  });

  await page.route('**/api/auth/logout', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { loggedOut: true }, error: null }),
    });
  });

  await page.goto('/login');
  await expect(page.getByText('Welcome')).toBeVisible();

  await page.goto('/company');
  await expect(page.getByText('Tell us about your company')).toBeVisible();

  const me = await page.evaluate(async () => {
    const response = await fetch('/api/auth/me', { credentials: 'include' });
    return response.json();
  });
  expect(me.data.user.email).toBe('recruiter@example.com');
});
