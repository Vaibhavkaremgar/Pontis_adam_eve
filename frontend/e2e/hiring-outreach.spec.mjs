import { expect, test } from '@playwright/test';

function makeCandidate(jobId, index, overrides = {}) {
  const baseId = `candidate-${index}`;
  return {
    id: baseId,
    name: `Candidate ${index}`,
    role: `Role ${index}`,
    headline: `Role ${index}`,
    company: `Company ${index}`,
    location: `Location ${index}`,
    email: `candidate-${index}@example.com`,
    fitScore: 4.9 - index * 0.1,
    strategy: 'HIGH',
    status: 'new',
    yearsExperience: 5 + index,
    skills: ['Python', 'FastAPI'],
    summary: `Profile for candidate ${index}.`,
    education: ['B.Tech'],
    projects: ['Project A'],
    certifications: ['AWS'],
    companiesHistory: ['Company X'],
    domainExperience: ['Platform'],
    resumeText: '',
    profileData: {},
    explanation: {
      semanticScore: 0.95,
      skillOverlap: 0.8,
      finalScore: 0.9,
      pdlRelevance: 0.8,
      recencyScore: 0.7,
      penalties: {},
      sourceBreakdown: {
        vector: 0.8,
        lexical: 0.7,
        recruiterPreference: 0.6,
        selectionRound: 0.5,
        voiceInterview: 0.4,
        freshness: 0.3,
      },
      recruiterPreferenceInfluence: 0,
      voiceInterviewInfluence: 0,
      lexicalRetrievalInfluence: 0,
      vectorRetrievalInfluence: 0,
      freshnessInfluence: 0,
      selectionRoundInfluence: 0,
      aiReasoning: '',
    },
    ...overrides,
  };
}

function buildSelectionSession({
  completed = false,
  currentBatchIndex = 0,
  selectedCandidateIds = [],
  rejectedCandidateIds = [],
  currentBatch = [],
  finalCandidates = [],
  analysis = null,
}) {
  return {
    sessionId: 'session-1',
    jobId: 'job-1',
    status: completed ? 'completed' : 'active',
    currentBatchIndex,
    totalBatches: 3,
    batchSize: 2,
    selectedCandidateIds,
    rejectedCandidateIds,
    currentBatch,
    analysis,
    completed,
    finalCandidates,
    topCandidates: finalCandidates,
    stage: completed ? 'final_shortlist' : 'dynamic_questioning',
    recommendedQuestions: ['What experience matters most?'],
    gapAnalysis: {
      missing_fields: [],
      ambiguous_fields: [],
      confidence_scores: {},
      missing_preferences: [],
      recommended_questions: ['What experience matters most?'],
    },
    intentProfile: {
      preferred_skills: ['Python', 'FastAPI'],
      preference_text: 'Prefer builders',
      voice_summary: 'Refined from voice',
      selection_round_count: 3,
    },
    telemetry: {
      preference_learning_gain: 0.1,
      rerank_precision_gain: 0.1,
      pair_signal_quality: 0.8,
      recruiter_preference_confidence: 0.75,
    },
    voiceSummary: 'Refined from voice',
    warning: null,
  };
}

test('review shortlist carries into outreach and queues the selected candidates', async ({ page }) => {
  const baseUrl = process.env.PLAYWRIGHT_BASE_URL?.trim() || '';
  const isLiveTarget = Boolean(baseUrl && !/localhost|127\.0\.0\.1/.test(baseUrl));
  if (isLiveTarget && (!process.env.PLAYWRIGHT_USERNAME || !process.env.PLAYWRIGHT_PASSWORD)) {
    test.skip(true, 'Live recruiter flow requires PLAYWRIGHT_USERNAME and PLAYWRIGHT_PASSWORD.');
  }

  const selectedIds = ['candidate-1', 'candidate-3', 'candidate-5'];
  const candidatePool = Array.from({ length: 6 }, (_, index) => makeCandidate('job-1', index + 1));
  const batch1 = candidatePool.slice(0, 2);
  const batch2 = candidatePool.slice(2, 4);
  const batch3 = candidatePool.slice(4, 6);
  const shortlistedCandidates = selectedIds.map((id) =>
    candidatePool.find((candidate) => candidate.id === id)
  ).filter(Boolean);
  const finalCandidates = candidatePool.map((candidate) =>
    selectedIds.includes(candidate.id)
      ? { ...candidate, status: 'shortlisted' }
      : { ...candidate, status: 'rejected' }
  );

  let selectionStep = 0;
  let sentOutreachPayload = null;
  const swipeCalls = [];
  const statusesByCandidate = Object.fromEntries(selectedIds.map((candidateId) => [candidateId, 'queued']));

  if (!isLiveTarget) {
  await page.addInitScript(({ jobId, shortlistedIds }) => {
    window.localStorage.setItem('pontis_user', JSON.stringify({ id: 'user-1', email: 'recruiter@example.com', role: 'admin', name: 'Recruiter' }));
    window.sessionStorage.setItem('pontis_job_id', jobId);
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
    window.sessionStorage.setItem('pontis_shortlist_job_id', jobId);
    window.sessionStorage.setItem('pontis_shortlist_ids', JSON.stringify(shortlistedIds));
  }, { jobId: 'job-1', shortlistedIds: selectedIds });

  await page.route('**/api/backend/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    const method = request.method();

    if (pathname === '/api/backend/auth/me' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { user: { id: 'user-1', email: 'recruiter@example.com', role: 'admin', name: 'Recruiter' } },
          error: null,
        }),
      });
      return;
    }

    if (pathname === '/api/backend/auth/csrf' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { token: 'test-csrf-token' },
          error: null,
        }),
      });
      return;
    }

    if (pathname === '/api/backend/candidates/selection/first' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: buildSelectionSession({
            completed: false,
            currentBatchIndex: 0,
            selectedCandidateIds: [],
            rejectedCandidateIds: [],
            currentBatch: batch1,
            finalCandidates: [],
          }),
          error: null,
        }),
      });
      return;
    }

    if (pathname === '/api/backend/candidates/selection' && method === 'POST') {
      const body = request.postDataJSON();
      selectionStep += 1;

      if (selectionStep === 1) {
        expect(body).toMatchObject({ jobId: 'job-1', candidateId: 'candidate-1' });
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            success: true,
            data: buildSelectionSession({
              completed: false,
              currentBatchIndex: 1,
              selectedCandidateIds: ['candidate-1'],
              rejectedCandidateIds: ['candidate-2'],
              currentBatch: batch2,
              finalCandidates: [],
            }),
            error: null,
          }),
        });
        return;
      }

      if (selectionStep === 2) {
        expect(body).toMatchObject({ jobId: 'job-1', candidateId: 'candidate-3' });
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            success: true,
            data: buildSelectionSession({
              completed: false,
              currentBatchIndex: 2,
              selectedCandidateIds: ['candidate-1', 'candidate-3'],
              rejectedCandidateIds: ['candidate-2', 'candidate-4'],
              currentBatch: batch3,
              finalCandidates: [],
            }),
            error: null,
          }),
        });
        return;
      }

      expect(body).toMatchObject({ jobId: 'job-1', candidateId: 'candidate-5' });
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: buildSelectionSession({
            completed: true,
            currentBatchIndex: 3,
            selectedCandidateIds: selectedIds,
            rejectedCandidateIds: ['candidate-2', 'candidate-4', 'candidate-6'],
            currentBatch: [],
            finalCandidates,
            analysis: {
              skillsOverlap: [],
              experienceTrends: {
                averageYears: 7,
                minimumYears: 5,
                maximumYears: 9,
                sampleSize: 3,
              },
              companySimilarities: { topCompanies: [] },
              roleAlignment: { topRoles: [] },
              preferenceSignals: {
                sharedSkills: ['Python', 'FastAPI'],
                sharedRoles: ['Engineer'],
                sharedCompanies: ['Company 1'],
              },
              summary: 'Selection preferences recorded from recruiter choices.',
            },
          }),
          error: null,
        }),
      });
      return;
    }

    if (pathname === '/api/backend/candidates/swipe' && method === 'POST') {
      const body = request.postDataJSON();
      swipeCalls.push(body);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            jobId: 'job-1',
            candidateId: body.candidateId,
            action: body.action,
            previousState: 'new',
            newState: 'shortlisted',
            message: 'Feedback recorded',
          },
          error: null,
        }),
      });
      return;
    }

    if (pathname === '/api/backend/candidates/shortlisted' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: shortlistedCandidates.map((candidate) => ({
            ...candidate,
            status: 'shortlisted',
            outreachStatus: statusesByCandidate[candidate.id] || 'pending',
          })),
          error: null,
        }),
      });
      return;
    }

    if (pathname === '/api/backend/outreach/preview' && method === 'GET') {
      const candidateId = url.searchParams.get('candidateId');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            subject: `Hello ${candidateId || 'candidate'}`,
            body: 'We would love to talk.',
            toEmail: `${candidateId || 'candidate'}@example.com`,
            candidateEmail: `${candidateId || 'candidate'}@example.com`,
            manualRequired: false,
          },
          error: null,
        }),
      });
      return;
    }

    if (pathname === '/api/backend/outreach' && method === 'POST') {
      sentOutreachPayload = request.postDataJSON();
      sentOutreachPayload.selectedCandidates.forEach((candidateId) => {
        statusesByCandidate[candidateId] = 'sent';
      });
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            success: true,
            processed: sentOutreachPayload.selectedCandidates.length,
            sent: sentOutreachPayload.selectedCandidates.length,
            skipped: 0,
            details: sentOutreachPayload.selectedCandidates.map((candidateId) => ({
              candidateId,
              status: 'sent',
              toEmail: `${candidateId}@example.com`,
            })),
            skippedCandidates: [],
            skipReasons: {},
            warnings: [],
            debug: {
              provider: 'resend',
              fromEmail: 'info@pontis.one',
              providerConfigured: true,
              dryRun: false,
            },
            job_id: 'job-1',
          },
          error: null,
        }),
      });
      return;
    }

    if (pathname === '/api/backend/outreach/status' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: selectedIds.map((candidateId) => ({
            candidateId,
            status: statusesByCandidate[candidateId] || 'queued',
            provider: 'resend',
            toEmail: `${candidateId}@example.com`,
            attemptCount: 1,
            lastSentAt: statusesByCandidate[candidateId] === 'sent' ? new Date().toISOString() : null,
            nextFollowUpAt: null,
            lastError: '',
          })),
          error: null,
        }),
      });
      return;
    }

    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({
        success: false,
        data: null,
        error: `Unhandled test route: ${pathname}`,
      }),
    });
  });
  }

  await page.goto('/review');
  await expect(page.getByText('Review Candidates')).toBeVisible();

  const selectCurrentBatchCandidate = async () => {
    const selectionButtons = page.locator('button').filter({ hasText: 'Select this candidate' });
    await expect(selectionButtons).toHaveCount(2);
    await selectionButtons.first().click();
  };

  await expect(page.getByText('Batch 1 of 3')).toBeVisible();
  await selectCurrentBatchCandidate();

  await expect(page.getByText('Batch 2 of 3')).toBeVisible();
  await selectCurrentBatchCandidate();

  await expect(page.getByText('Batch 3 of 3')).toBeVisible();
  await selectCurrentBatchCandidate();

  await expect(page.getByText('Selection complete')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue to Outreach' })).toBeVisible();
  await page.getByRole('button', { name: 'Continue to Outreach' }).click();

  await expect(page).toHaveURL(/\/outreach/);
  await expect(page.getByText('Candidate Outreach')).toBeVisible();
  await expect(page.locator('input[type="checkbox"]:checked')).toHaveCount(3);
  await expect(page.getByText('3 shortlisted candidates selected for outreach.')).toBeVisible();

  await page.getByRole('button', { name: 'Send Outreach' }).click();
  await expect(page.getByText('Outreach processed: 3 sent, 0 skipped.')).toBeVisible();

  expect(swipeCalls).toHaveLength(3);
  expect(swipeCalls.map((item) => item.candidateId).sort()).toEqual(selectedIds.slice().sort());
  expect(sentOutreachPayload).not.toBeNull();
  expect(sentOutreachPayload.selectedCandidates).toEqual(selectedIds);
  expect(sentOutreachPayload.customBody).toBe('');
});
