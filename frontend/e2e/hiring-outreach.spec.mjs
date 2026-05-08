import { expect, test } from '@playwright/test';

test('hiring flow renders company and outreach controls', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('pontis_user', JSON.stringify({ id: 'user-1', email: 'recruiter@example.com', role: 'admin' }));
    window.sessionStorage.setItem('pontis_job_id', 'job-1');
    window.sessionStorage.setItem('pontis_is_refined', 'true');
  });

  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { user: { id: 'user-1', email: 'recruiter@example.com', role: 'admin' } }, error: null }),
    });
  });

  await page.route('**/api/company', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          name: 'Acme',
          website: 'https://acme.test',
          description: 'Test company',
          atsProvider: 'mock',
          atsConnected: true,
        },
        error: null,
      }),
    });
  });

  await page.route('**/api/candidates/shortlisted*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: [{ id: 'candidate-1', name: 'Avery', role: 'Platform Engineer', company: 'Northstar', status: 'shortlisted' }], error: null }),
    });
  });

  await page.route('**/api/outreach/preview*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { subject: 'Hello Avery', body: 'We would love to talk.', toEmail: 'avery@example.com', usingFallbackEmail: false },
        error: null,
      }),
    });
  });

  await page.route('**/api/outreach/queue', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { selected_count: 1 }, error: null }),
    });
  });

  await page.goto('/outreach?skipVoice=1');
  await expect(page.getByText('Candidate Outreach')).toBeVisible();
});
