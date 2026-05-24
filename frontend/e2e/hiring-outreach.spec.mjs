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
  const outreachIds = ['candidate-2', 'candidate-4', 'candidate-6'];
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
  let calibrationStep = 0;
  const sentOutreachPayloads = [];
  const swipeCalls = [];
  const statusesByCandidate = Object.fromEntries(outreachIds.map((candidateId) => [candidateId, 'queued']));
  const calibrationSets = [
    {
      set_title: 'Calibration set 1',
      set_theme: 'Startup builders vs enterprise systems',
      archetypes: [
        {
          id: 'archetype-startup-builder',
          title: 'Startup builder',
          strengths: ['Fast shipping', 'Ambiguous problem solving'],
          work_style: 'Hands-on, high cadence',
          ownership_level: 'Very high',
          ideal_environment: 'Seed to Series A',
          communication_style: 'Direct and concise',
          execution_style: 'Moves fast with partial information',
          risk_tolerance: 'High',
          leadership_signals: ['Initiates', 'Unblocks', 'Builds momentum'],
        },
        {
          id: 'archetype-enterprise-specialist',
          title: 'Enterprise systems specialist',
          strengths: ['Reliability', 'Cross-team coordination'],
          work_style: 'Structured and deliberate',
          ownership_level: 'High',
          ideal_environment: 'Enterprise platform teams',
          communication_style: 'Clear and thorough',
          execution_style: 'Plans deeply before delivery',
          risk_tolerance: 'Moderate',
          leadership_signals: ['Aligns stakeholders', 'Documents rigorously'],
        },
      ],
    },
    {
      set_title: 'Calibration set 2',
      set_theme: 'Execution-heavy operator vs product-focused engineer',
      archetypes: [
        {
          id: 'archetype-operator',
          title: 'Execution-heavy operator',
          strengths: ['Delivery pressure', 'Process follow-through'],
          work_style: 'High ownership, outcome-driven',
          ownership_level: 'Very high',
          ideal_environment: 'Scaling teams with urgency',
          communication_style: 'Tactical and crisp',
          execution_style: 'Turns ambiguity into action',
          risk_tolerance: 'Moderate to high',
          leadership_signals: ['Operates with urgency', 'Keeps teams moving'],
        },
        {
          id: 'archetype-product-engineer',
          title: 'Product-focused engineer',
          strengths: ['User empathy', 'Product judgment'],
          work_style: 'Collaborative and iterative',
          ownership_level: 'High',
          ideal_environment: 'Product-led teams',
          communication_style: 'Thoughtful and contextual',
          execution_style: 'Balances speed with product quality',
          risk_tolerance: 'Moderate',
          leadership_signals: ['Questions assumptions', 'Shapes product direction'],
        },
      ],
    },
    {
      set_title: 'Calibration set 3',
      set_theme: 'AI-native infra engineer vs leadership-heavy architect',
      archetypes: [
        {
          id: 'archetype-ai-infra',
          title: 'AI-native infra engineer',
          strengths: ['LLM systems', 'Operational reliability'],
          work_style: 'Deep technical focus',
          ownership_level: 'High',
          ideal_environment: 'AI infra and platform orgs',
          communication_style: 'Technical and precise',
          execution_style: 'Builds systems that scale with usage',
          risk_tolerance: 'Moderate',
          leadership_signals: ['Anticipates failure modes', 'Improves platform leverage'],
        },
        {
          id: 'archetype-architect',
          title: 'Leadership-heavy architect',
          strengths: ['Architecture', 'Cross-functional leadership'],
          work_style: 'Strategic and collaborative',
          ownership_level: 'Very high',
          ideal_environment: 'Complex multi-team orgs',
          communication_style: 'Structured and influential',
          execution_style: 'Creates alignment before execution',
          risk_tolerance: 'Low to moderate',
          leadership_signals: ['Sets direction', 'Mentors teams', 'Drives standards'],
        },
      ],
    },
  ];

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

    if (pathname === '/api/backend/recruiters/user-1/intelligence/jobs/job-1' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            calibration: {
              stage: 'archetype_calibration',
              current_round_index: 1,
              archetype_sets: calibrationSets,
              current_pair: calibrationSets[0],
            },
          },
          error: null,
        }),
      });
      return;
    }

    if (pathname === '/api/backend/recruiters/user-1/intelligence/jobs/job-1/choice' && method === 'POST') {
      calibrationStep += 1;
      const nextIndex = Math.min(calibrationStep + 1, calibrationSets.length);
      const nextSet = calibrationSets[Math.min(calibrationStep, calibrationSets.length - 1)];
      const isComplete = calibrationStep >= calibrationSets.length;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            calibration: {
              stage: isComplete ? 'real_sourcing_ready' : 'archetype_calibration',
              current_round_index: nextIndex,
              archetype_sets: calibrationSets,
              current_pair: isComplete ? null : nextSet,
            },
          },
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
      const sentOutreachPayload = request.postDataJSON();
      sentOutreachPayloads.push(sentOutreachPayload);
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
          data: outreachIds.map((candidateId) => ({
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

    if (pathname === '/api/backend/interviews' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: outreachIds.map((candidateId) => ({
            candidateId,
            name: `Candidate ${candidateId.slice(-1)}`,
            status: statusesByCandidate[candidateId] === 'sent' ? 'contacted' : 'shortlisted',
          })),
          error: null,
        }),
      });
      return;
    }

    if (pathname === '/api/backend/metrics' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            emails_sent: outreachIds.length,
            replies_received: 0,
            interviews_booked: 0,
            conversion_rate: 0,
          },
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

  await expect(page.getByText('Preference calibration 1 / 3')).toBeVisible();
  await expect(page.getByTestId('calibration-select-archetype-startup-builder')).toBeVisible();
  await page.getByTestId('calibration-select-archetype-startup-builder').click();
  await expect(page.getByText('Preference calibration 2 / 3')).toBeVisible();
  await page.getByTestId('calibration-select-archetype-operator').click();
  await expect(page.getByText('Preference calibration 3 / 3')).toBeVisible();
  await page.getByTestId('calibration-select-archetype-ai-infra').click();

  const selectCurrentBatchCandidate = async () => {
    const candidateId = ['candidate-1', 'candidate-3', 'candidate-5'][selectionStep];
    await page.getByTestId(`batch-select-${candidateId}`).click();
  };

  await expect(page.getByText('Batch 1 of 3')).toBeVisible();
  await selectCurrentBatchCandidate();

  await expect(page.getByText('Batch 2 of 3')).toBeVisible();
  await selectCurrentBatchCandidate();

  await expect(page.getByText('Batch 3 of 3')).toBeVisible();
  await selectCurrentBatchCandidate();

  await expect(page.getByText('Selection complete')).toBeVisible();

  const selectFinalCandidate = async (candidateName) => {
    const candidateId =
      candidateName === 'Candidate 2'
        ? 'candidate-2'
        : candidateName === 'Candidate 4'
          ? 'candidate-4'
          : 'candidate-6';
    await page.getByTestId(`review-select-${candidateId}`).click();
  };

  await selectFinalCandidate('Candidate 2');
  await selectFinalCandidate('Candidate 4');
  await selectFinalCandidate('Candidate 6');

  await expect(page.getByText('Outreach sent to 1 candidate.')).toBeVisible();
  await expect(page.getByTestId('continue-to-outreach')).toBeVisible();
  await page.getByTestId('continue-to-outreach').click();

  await expect(page).toHaveURL(/\/ready/);
  await expect(page.getByText('Candidates ready for interview')).toBeVisible();
  await expect(page.getByText('3 records')).toBeVisible();
  await expect(page.getByText('candidate-2@example.com')).toBeVisible();
  await expect(page.getByText('candidate-4@example.com')).toBeVisible();
  await expect(page.getByText('candidate-6@example.com')).toBeVisible();

  expect(swipeCalls).toHaveLength(3);
  expect(swipeCalls.map((item) => item.candidateId).sort()).toEqual(outreachIds.slice().sort());
  expect(sentOutreachPayloads).toHaveLength(3);
  expect(sentOutreachPayloads.map((item) => item.selectedCandidates[0]).sort()).toEqual(outreachIds.slice().sort());
  expect(sentOutreachPayloads.every((item) => !item.customBody)).toBe(true);
});
