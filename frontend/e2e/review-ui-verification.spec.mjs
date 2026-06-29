import { expect, test } from '@playwright/test';

function buildCandidate(index) {
  return {
    id: `candidate-${index}`,
    name: `Candidate ${index}`,
    role: `Backend Engineer ${index}`,
    headline: `Backend Engineer ${index}`,
    company: `Company ${index}`,
    currentCompany: `Company ${index}`,
    location: index % 2 === 0 ? 'Remote' : 'Bengaluru',
    email: `${index}@example.com`,
    yearsExperience: 5 + index,
    skills: ['Python', 'FastAPI', 'PostgreSQL'],
    summary: `Candidate ${index} summary.`,
    status: 'new',
    profileData: {
      linkedin_url: `https://www.linkedin.com/in/candidate-${index}/`,
      linkedinUrl: `https://www.linkedin.com/in/candidate-${index}/`,
      currentCompany: `Company ${index}`,
      location: index % 2 === 0 ? 'Remote' : 'Bengaluru',
      skills: ['Python', 'FastAPI', 'PostgreSQL'],
    },
    rawDiscovery: {},
    explanation: {
      semanticScore: 0.9,
      skillOverlap: 0.8,
      finalScore: 0.9,
      pdlRelevance: 0.7,
      recencyScore: 0.6,
      penalties: {},
      sourceBreakdown: {},
      skillsMatched: ['Python', 'FastAPI'],
      aiReasoning: 'Strong match for the role.',
    },
  };
}

async function installReviewFixtures(page) {
  await page.addInitScript(() => {
    window.localStorage.setItem('pontis_user', JSON.stringify({ id: 'user-1', email: 'recruiter@example.com', role: 'admin', name: 'Recruiter' }));
    window.sessionStorage.setItem('pontis_job_id', 'job-1');
    window.sessionStorage.setItem('pontis_is_refined', 'true');
    window.sessionStorage.setItem(
      'pontis_job',
      JSON.stringify({
        title: 'Backend Engineer',
        description: 'Build reliable systems.',
        location: 'Remote',
        compensation: 'Competitive',
        workAuthorization: 'required',
        remotePolicy: 'hybrid',
        experienceRequired: '5+ years',
        vettingMode: 'volume',
        autoExportToAts: false,
      })
    );
    window.sessionStorage.setItem(
      'pontis_company',
      JSON.stringify({
        name: 'Acme',
        website: 'https://acme.test',
        description: 'Test company',
        industry: 'Software',
        atsProvider: 'mock',
        atsConnected: true,
      })
    );
  });

  const candidates = Array.from({ length: 10 }, (_, index) => buildCandidate(index + 1));

  await page.route('**/api/backend/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { user: { id: 'user-1', email: 'recruiter@example.com', role: 'admin', name: 'Recruiter' } }, error: null }),
    });
  });

  await page.route('**/api/backend/auth/csrf', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { token: 'test-csrf' }, error: null }),
    });
  });

  await page.route('**/api/backend/recruiters/user-1/intelligence/jobs/job-1', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          calibration: {
            stage: 'real_sourcing_ready',
            current_round_index: 3,
            archetype_sets: [],
            current_pair: null,
            voice_summary: 'Looking for a senior backend engineer with strong API design, AWS, and PostgreSQL experience.',
          },
          interview: {
            voice_summary: 'Looking for a senior backend engineer with strong API design, AWS, and PostgreSQL experience.',
          },
        },
        error: null,
      }),
    });
  });

  await page.route('**/api/backend/candidates?**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== '/api/backend/candidates') {
      await route.continue();
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: candidates, error: null }),
    });
  });

  await page.route('**/api/backend/candidates/swipe', async (route) => {
    const body = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          jobId: body.jobId,
          candidateId: body.candidateId,
          action: body.action,
          previousState: 'new',
          newState: body.action === 'accept' ? 'shortlisted' : 'rejected',
          message: 'ok',
        },
        error: null,
      }),
    });
  });

  await page.route('**/api/backend/candidates/select', async (route) => {
    const body = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { jobId: body.jobId, candidateId: body.candidateId }, error: null }),
    });
  });

  await page.route('**/api/backend/interviews*', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/backend/interviews') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: [], error: null }),
      });
      return;
    }
    if (url.pathname === '/api/backend/interview/insights') {
      const candidateId = url.searchParams.get('candidateId') || '';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            jobId: 'job-1',
            candidateId,
            currentStage: 'screening',
            progression: [],
            evaluations: [],
            intelligence: {},
            currentSession: null,
            stageHistory: [],
          },
          error: null,
        }),
      });
      return;
    }
    await route.continue();
  });

  await page.route('**/api/backend/ats/timeline*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: [], error: null }),
    });
  });

  await page.route('**/api/backend/ats/notifications*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: [], error: null }),
    });
  });

  await page.context().route('**://www.linkedin.com/**', async (route) => {
    const url = route.request().url();
    await route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: `<html><head><title>LinkedIn</title></head><body>LinkedIn stub: ${url}</body></html>`,
    });
  });
}

async function openReviewPage(page) {
  await page.goto('/review');
  await expect(page.getByText('Review Candidates')).toBeVisible();
  await expect(page.getByText('Swipe to shortlist')).toBeVisible();
  await expect(page.getByText('Voice intake summary')).toBeVisible();
}

async function getCounts(page) {
  const countsText = await page.locator('text=remaining').first().textContent();
  return countsText || '';
}

async function expectCounts(page, remaining, shortlisted) {
  const countsLine = page.locator('p').filter({ hasText: /remaining/ }).first();
  await expect(countsLine).toBeVisible();
  await expect(countsLine).toContainText(`${remaining} remaining`);
  await expect(countsLine).toContainText(`${shortlisted} shortlisted`);
}

test.describe('review page interaction verification', () => {
  test('desktop behavior', async ({ page }) => {
    await installReviewFixtures(page);
    await openReviewPage(page);

    await expectCounts(page, 10, 0);

    const currentCard = page.getByText('Candidate 1').first();
    await expect(currentCard).toBeVisible();

    await expect(page.getByRole('dialog')).toHaveCount(0);

    const linkedinLink = page.getByRole('link', { name: /LinkedIn/i }).first();
    const popupPromise = page.context().waitForEvent('page');
    await linkedinLink.click();
    const popup = await popupPromise;
    await popup.waitForLoadState();
    await expect(popup).toHaveURL(/linkedin\.com\/in\/candidate-1/);
    await expect(page.getByRole('dialog')).toHaveCount(0);

    await page.getByRole('button', { name: 'Reject' }).click();
    await expectCounts(page, 9, 0);
    await expect(page.getByText('Candidate 1')).toHaveCount(0);
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Shortlist' })).toBeEnabled();

    await page.getByRole('button', { name: 'Shortlist' }).click();
    await expectCounts(page, 8, 1);
    await expect(page.getByText('Candidate 2')).toHaveCount(0);
    await expect(page.getByRole('dialog')).toHaveCount(0);

    await page.getByText('Candidate 3').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByRole('dialog').getByRole('button', { name: 'Close' })).toBeVisible();
    await page.getByRole('button', { name: 'Close' }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);

    await page.reload();
    await expectCounts(page, 8, 1);
    await expect(page.getByText('Candidate 1')).toHaveCount(0);
    await expect(page.getByText('Candidate 2')).toHaveCount(0);
  });

  test('mobile tap behavior', async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 },
      hasTouch: true,
      isMobile: true,
    });
    const page = await context.newPage();
    await installReviewFixtures(page);
    await openReviewPage(page);

    await expectCounts(page, 10, 0);
    await page.getByRole('button', { name: 'Shortlist' }).tap();
    await expectCounts(page, 9, 1);

    await page.getByRole('button', { name: 'Reject' }).tap();
    await expectCounts(page, 8, 1);

    await context.close();
  });
});
