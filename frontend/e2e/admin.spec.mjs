import { expect, test } from '@playwright/test';

test('admin dashboard shows diagnostics and replay surfaces', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('pontis_user', JSON.stringify({ id: 'user-1', email: 'admin@example.com', role: 'admin' }));
  });

  await page.route('**/api/backend/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { user: { id: 'user-1', email: 'admin@example.com', role: 'admin' } }, error: null }),
    });
  });

  await page.route('**/api/admin/diagnostics', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          config: { environment: 'test' },
          queue: { status: 'ok', workers: 1 },
          metrics: { events: 3, ai_observability: { ranking_drifts: 1, retrieval_quality_events: 2, embedding_drift_events: 0, avg_queue_ai_latency: 0.15 } },
          qdrant: { status: 'ok' },
          llm: { status: 'ok' },
        },
        error: null,
      }),
    });
  });

  await page.route('**/api/admin/queue/deadletters*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: [{ queueType: 'outreach_send', jobId: 'job-1', lastError: 'timeout' }], error: null }),
    });
  });

  await page.route('**/api/admin/outreach/analytics*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { replyRate: 0.2, bounceRate: 0.01, counts: { sent: 1, opened: 1 } }, error: null }),
    });
  });

  await page.route('**/api/admin/audit*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: [{ action: 'admin_replay_dead_letter', entityType: 'queue' }], error: null }),
    });
  });

  await page.goto('/admin');
  await expect(page.getByText('Platform Admin')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Dead Letters' })).toBeVisible();
});
