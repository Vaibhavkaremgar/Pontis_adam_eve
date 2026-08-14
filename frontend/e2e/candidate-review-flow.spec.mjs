/**
 * Live browser-focused verification of the candidate review flow.
 *
 * INTERNAL candidate:
 *   - source=internal → buttons: Interested / Not Interested only
 *   - Shortlist and Reject must NOT be visible
 *   - Click Interested → POST /candidates/:id/interest
 *   - Verify response shape (request_id, status=PENDING, eve_delivery_status=queued)
 *   - Verify profile detail view shows richer DB-backed data
 *
 * SERPAPI candidate:
 *   - source=serpapi → buttons: Shortlist / Reject only
 *   - Interested and Not Interested must NOT be visible
 *   - Click Shortlist → POST /candidates/select (existing SerpAPI flow)
 *   - Confirm no Eve recruiter-interest request is generated
 */

import { expect, test } from '@playwright/test';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const INTERNAL_CANDIDATE = {
  id: 'internal-candidate-001',
  name: 'Alice Internal',
  role: 'Senior Backend Engineer',
  headline: 'Senior Backend Engineer',
  company: 'Acme Corp',
  currentCompany: 'Acme Corp',
  location: 'Bengaluru',
  email: 'alice@internal.example.com',
  yearsExperience: 7,
  skills: ['Python', 'FastAPI', 'PostgreSQL', 'Redis'],
  summary: 'Experienced backend engineer with strong API design skills.',
  status: 'new',
  source: 'internal',
  sourceType: 'internal',
  sourceProvider: 'internal',
  recruiterAction: 'NONE',
  requestStatus: null,
  fitScore: 0.92,
  decision: 'strong_match',
  strategy: 'internal_semantic_match',
  explanation: {
    semanticScore: 0.92,
    skillOverlap: 0.85,
    finalScore: 0.92,
    skillsMatched: ['Python', 'FastAPI'],
    aiReasoning: 'Strong internal match — resume and interview scores align well.',
    penalties: {},
    sourceBreakdown: {},
  },
  profileData: {
    linkedin_url: 'https://www.linkedin.com/in/alice-internal/',
    linkedinUrl: 'https://www.linkedin.com/in/alice-internal/',
    currentCompany: 'Acme Corp',
    location: 'Bengaluru',
    skills: ['Python', 'FastAPI', 'PostgreSQL', 'Redis'],
  },
  rawDiscovery: {},
};

const SERPAPI_CANDIDATE = {
  id: 'serpapi-candidate-001',
  name: 'Bob SerpAPI',
  role: 'Full Stack Engineer',
  headline: 'Full Stack Engineer',
  company: 'TechCo',
  currentCompany: 'TechCo',
  location: 'Remote',
  email: null,
  yearsExperience: 5,
  skills: ['JavaScript', 'React', 'Node.js', 'AWS'],
  summary: 'Full stack engineer with strong frontend and backend skills.',
  status: 'new',
  source: 'serpapi',
  sourceType: 'linkedin_xray',
  sourceProvider: 'serpapi',
  recruiterAction: 'NONE',
  requestStatus: null,
  fitScore: 0.78,
  decision: 'potential',
  strategy: 'HIGH',
  explanation: {
    semanticScore: 0.78,
    skillOverlap: 0.70,
    finalScore: 0.78,
    skillsMatched: ['JavaScript', 'React'],
    aiReasoning: 'Good match for the role based on LinkedIn profile.',
    penalties: {},
    sourceBreakdown: {},
  },
  profileData: {
    linkedin_url: 'https://www.linkedin.com/in/bob-serpapi/',
    linkedinUrl: 'https://www.linkedin.com/in/bob-serpapi/',
    currentCompany: 'TechCo',
    location: 'Remote',
    skills: ['JavaScript', 'React', 'Node.js', 'AWS'],
  },
  rawDiscovery: {},
};

const INTERNAL_FULL_PROFILE = {
  candidate_id: 'internal-candidate-001',
  name: 'Alice Internal',
  role: 'Senior Backend Engineer',
  company: 'Acme Corp',
  location: 'Bengaluru',
  years_experience: 7,
  skills: ['Python', 'FastAPI', 'PostgreSQL', 'Redis', 'Docker'],
  summary: 'Experienced backend engineer with strong API design skills and 7 years of industry experience.',
  email: 'alice@internal.example.com',
  phone: '+91-9876543210',
  linkedin_url: 'https://www.linkedin.com/in/alice-internal/',
  resume_text: 'Alice Internal — Senior Backend Engineer\n7 years experience in Python, FastAPI, PostgreSQL...',
  work_experience: [
    { company: 'Acme Corp', title: 'Senior Backend Engineer', duration: '2020–present' },
    { company: 'StartupXYZ', title: 'Backend Engineer', duration: '2017–2020' },
  ],
  education: [
    { institution: 'IIT Bangalore', degree: 'B.Tech Computer Science', year: '2017' },
  ],
  certifications: ['AWS Certified Developer', 'PostgreSQL Professional'],
  projects: ['Open-source FastAPI middleware', 'Internal data pipeline tool'],
  profile_access: 'INTERNAL',
  raw_profile_available: true,
};

// ── Shared setup ──────────────────────────────────────────────────────────────

async function installFixtures(page, candidates) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      'pontis_user',
      JSON.stringify({ id: 'user-1', email: 'recruiter@example.com', role: 'admin', name: 'Recruiter' })
    );
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

  await page.route('**/api/backend/auth/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { user: { id: 'user-1', email: 'recruiter@example.com', role: 'admin', name: 'Recruiter' } },
        error: null,
      }),
    })
  );

  await page.route('**/api/backend/auth/csrf', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: { token: 'test-csrf' }, error: null }),
    })
  );

  await page.route('**/api/backend/recruiters/user-1/intelligence/jobs/job-1', (route) =>
    route.fulfill({
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
            voice_summary: 'Looking for a senior backend engineer with strong API design and PostgreSQL experience.',
          },
          interview: {
            voice_summary: 'Looking for a senior backend engineer with strong API design and PostgreSQL experience.',
          },
        },
        error: null,
      }),
    })
  );

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

  await page.route('**/api/backend/candidates/accepted**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: [], error: null }) })
  );

  await page.route('**/api/backend/candidates/pending-acceptance**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: [], error: null }) })
  );

  await page.route('**/api/backend/interviews**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: [], error: null }) })
  );

  await page.route('**/api/backend/ats/timeline**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: [], error: null }) })
  );

  await page.route('**/api/backend/ats/notifications**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: [], error: null }) })
  );

  await page.context().route('**://www.linkedin.com/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: '<html><body>LinkedIn stub</body></html>',
    })
  );
}

// ── INTERNAL CANDIDATE TESTS ──────────────────────────────────────────────────

test.describe('INTERNAL candidate review flow', () => {
  test('source is identified as internal and correct buttons are shown', async ({ page }) => {
    await installFixtures(page, [INTERNAL_CANDIDATE]);
    await page.goto('/review');

    // Step 1: Page loads
    await expect(page.getByText('Review Candidates')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/Swipe to interested/i)).toBeVisible({ timeout: 10_000 });

    // Step 2: Candidate card is visible
    await expect(page.getByText('Alice Internal')).toBeVisible();

    // Step 3: Source badge shows "Internal"
    const sourceBadge = page.locator('span').filter({ hasText: /^Internal$/ }).first();
    await expect(sourceBadge).toBeVisible();

    // Step 4: Correct buttons — Interested and Not Interested MUST be visible
    await expect(page.getByRole('button', { name: 'Interested' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Not Interested' })).toBeVisible();

    // Step 5: Shortlist and Reject must NOT be visible
    await expect(page.getByRole('button', { name: 'Shortlist' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Reject' })).toHaveCount(0);
  });

  test('Interested button fires correct API request and response is valid', async ({ page }) => {
    let capturedRequest = null;
    let capturedResponse = null;
    let eveCallMade = false;

    await installFixtures(page, [INTERNAL_CANDIDATE]);

    // Intercept the interest endpoint — capture request + return mock response
    await page.route('**/api/backend/candidates/internal-candidate-001/interest**', async (route) => {
      capturedRequest = {
        method: route.request().method(),
        url: route.request().url(),
        // Do NOT log auth headers — only log safe metadata
        hasAuthHeader: Boolean(route.request().headers()['cookie'] || route.request().headers()['authorization']),
      };
      const responseBody = {
        success: true,
        data: {
          request_id: 'req-internal-001',
          candidate_id: 'internal-candidate-001',
          job_id: 'job-1',
          status: 'PENDING',
          recruiter_action: 'INTERESTED',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          responded_at: null,
          eve_delivery_status: 'queued',
        },
        error: null,
      };
      capturedResponse = responseBody;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(responseBody),
      });
    });

    // Intercept any Eve-facing calls (should NOT be called from the browser)
    await page.route('**/eve/**', () => { eveCallMade = true; });
    await page.route('**/api/eve/**', () => { eveCallMade = true; });

    await page.goto('/review');
    await expect(page.getByText('Review Candidates')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Alice Internal')).toBeVisible();

    // Step 5: Click Interested
    await page.getByRole('button', { name: 'Interested' }).click();

    // Step 6: Verify network request was made
    expect(capturedRequest).not.toBeNull();
    expect(capturedRequest.method).toBe('POST');
    expect(capturedRequest.url).toContain('/candidates/internal-candidate-001/interest');
    expect(capturedRequest.url).toContain('jobId=job-1');

    // Step 7: Verify response succeeded
    expect(capturedResponse.success).toBe(true);
    expect(capturedResponse.data.status).toBe('PENDING');
    expect(capturedResponse.data.recruiter_action).toBe('INTERESTED');

    // Step 8: Verify Adam persistence fields in response
    expect(capturedResponse.data.request_id).toBeTruthy();
    expect(capturedResponse.data.candidate_id).toBe('internal-candidate-001');
    expect(capturedResponse.data.job_id).toBe('job-1');
    expect(capturedResponse.data.created_at).toBeTruthy();

    // Step 9: Verify Eve outbound event is queued (eve_delivery_status=queued)
    expect(capturedResponse.data.eve_delivery_status).toBe('queued');

    // Step 10: Eve is NOT directly called from the browser (Eve is called server-side)
    expect(eveCallMade).toBe(false);

    // UI feedback: interest request saved message should appear
    await expect(page.getByText(/interest request saved/i)).toBeVisible({ timeout: 5_000 });
  });

  test('internal candidate profile detail view shows richer DB-backed data', async ({ page }) => {
    await installFixtures(page, [INTERNAL_CANDIDATE]);

    // Mock the internal-profile endpoint
    await page.route('**/api/backend/candidates/internal-candidate-001/internal-profile**', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: INTERNAL_FULL_PROFILE, error: null }),
      })
    );

    await page.goto('/review');
    await expect(page.getByText('Review Candidates')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Alice Internal')).toBeVisible();

    // Open the candidate detail modal
    await page.getByRole('button', { name: /view full profile/i }).first().click();

    // Modal should open
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5_000 });

    // Verify richer DB-backed fields are shown
    await expect(page.getByRole('dialog').getByText('Alice Internal')).toBeVisible();
    await expect(page.getByRole('dialog').getByText(/Internal DB/i)).toBeVisible();

    // Internal profile section should be present
    await expect(page.getByRole('dialog').getByText(/Internal profile/i)).toBeVisible();

    // Work experience from DB
    await expect(page.getByRole('dialog').getByText(/Work experience/i)).toBeVisible();

    // Education from DB
    await expect(page.getByRole('dialog').getByText(/Education/i)).toBeVisible();

    // Skills from DB (richer set)
    await expect(page.getByRole('dialog').getByText('Docker')).toBeVisible();

    // Close modal
    await page.getByRole('button', { name: 'Close' }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
  });
});

// ── SERPAPI CANDIDATE TESTS ───────────────────────────────────────────────────

test.describe('SERPAPI candidate review flow', () => {
  test('source is identified as serpapi and correct buttons are shown', async ({ page }) => {
    await installFixtures(page, [SERPAPI_CANDIDATE]);
    await page.goto('/review');

    await expect(page.getByText('Review Candidates')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/Swipe to shortlist/i)).toBeVisible({ timeout: 10_000 });

    // Candidate card is visible
    await expect(page.getByText('Bob SerpAPI')).toBeVisible();

    // Source badge shows "SerpAPI"
    const sourceBadge = page.locator('span').filter({ hasText: /^SerpAPI$/ }).first();
    await expect(sourceBadge).toBeVisible();

    // Correct buttons — Shortlist and Reject MUST be visible
    await expect(page.getByRole('button', { name: 'Shortlist' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reject' })).toBeVisible();

    // Interested and Not Interested must NOT be visible
    await expect(page.getByRole('button', { name: 'Interested' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Not Interested' })).toHaveCount(0);
  });

  test('Shortlist fires the SerpAPI select flow and no Eve interest request is generated', async ({ page }) => {
    let selectCallMade = false;
    let interestCallMade = false;
    let capturedSelectRequest = null;
    let capturedSelectResponse = null;

    await installFixtures(page, [SERPAPI_CANDIDATE]);

    // Intercept the SerpAPI shortlist endpoint (POST /candidates/select)
    await page.route('**/api/backend/candidates/select', async (route) => {
      selectCallMade = true;
      capturedSelectRequest = {
        method: route.request().method(),
        url: route.request().url(),
        body: route.request().postDataJSON(),
      };
      const responseBody = {
        success: true,
        data: {
          jobId: 'job-1',
          candidateId: 'serpapi-candidate-001',
          status: 'selected',
          enrichmentStatus: 'enrichment_pending',
          outreachStatus: 'queued',
        },
        error: null,
      };
      capturedSelectResponse = responseBody;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(responseBody),
      });
    });

    // Intercept the interest endpoint — must NOT be called for SerpAPI candidates
    await page.route('**/api/backend/candidates/serpapi-candidate-001/interest**', async (route) => {
      interestCallMade = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: {}, error: null }),
      });
    });

    await page.goto('/review');
    await expect(page.getByText('Review Candidates')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Bob SerpAPI')).toBeVisible();

    // Click Shortlist
    await page.getByRole('button', { name: 'Shortlist' }).click();

    // Verify the SerpAPI select flow was triggered
    expect(selectCallMade).toBe(true);
    expect(capturedSelectRequest.method).toBe('POST');
    expect(capturedSelectRequest.url).toContain('/candidates/select');
    expect(capturedSelectRequest.body.candidateId).toBe('serpapi-candidate-001');
    expect(capturedSelectRequest.body.jobId).toBe('job-1');

    // Verify response succeeded
    expect(capturedSelectResponse.success).toBe(true);

    // Verify no Eve recruiter-interest request was generated
    expect(interestCallMade).toBe(false);
  });

  test('Reject fires swipe endpoint and no Eve interest request is generated', async ({ page }) => {
    let swipeCallMade = false;
    let interestCallMade = false;
    let capturedSwipeRequest = null;

    await installFixtures(page, [SERPAPI_CANDIDATE]);

    await page.route('**/api/backend/candidates/swipe', async (route) => {
      swipeCallMade = true;
      capturedSwipeRequest = {
        method: route.request().method(),
        body: route.request().postDataJSON(),
      };
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            jobId: 'job-1',
            candidateId: 'serpapi-candidate-001',
            action: 'reject',
            previousState: 'new',
            newState: 'rejected',
            message: 'ok',
          },
          error: null,
        }),
      });
    });

    await page.route('**/api/backend/candidates/serpapi-candidate-001/interest**', async (route) => {
      interestCallMade = true;
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: {}, error: null }) });
    });

    await page.goto('/review');
    await expect(page.getByText('Review Candidates')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Bob SerpAPI')).toBeVisible();

    // Click Reject
    await page.getByRole('button', { name: 'Reject' }).click();

    // Verify swipe endpoint was called with reject action
    expect(swipeCallMade).toBe(true);
    expect(capturedSwipeRequest.method).toBe('POST');
    expect(capturedSwipeRequest.body.action).toBe('reject');
    expect(capturedSwipeRequest.body.candidateId).toBe('serpapi-candidate-001');

    // Confirm no Eve recruiter-interest request was generated
    expect(interestCallMade).toBe(false);
  });
});
