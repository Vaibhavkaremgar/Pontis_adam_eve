# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: candidate-review-flow.spec.mjs >> SERPAPI candidate review flow >> source is identified as serpapi and correct buttons are shown
- Location: e2e\candidate-review-flow.spec.mjs:400:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('span').filter({ hasText: /^SerpAPI$/ }).first()
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for locator('span').filter({ hasText: /^SerpAPI$/ }).first()

```

```yaml
- banner:
  - link "Back to Voice Intake":
    - /url: /voice
  - link "Pontis":
    - /url: /company
  - button "Sign out"
- text: "1"
- paragraph: Company
- text: "2"
- paragraph: Job
- text: "3"
- paragraph: Voice
- text: "4"
- paragraph: Review
- text: "5"
- paragraph: Ready
- text: "6"
- paragraph: Results
- main:
  - heading "Review Candidates" [level=1]
  - paragraph: A refined shortlist review built for fast, confident selection.
  - button "Voice intake summary"
  - paragraph: Candidate review
  - paragraph: For a better understanding of each candidate and their work, please open the LinkedIn profile from the LinkedIn button on the card before making your choice.
  - paragraph: Swipe to shortlist
  - paragraph: 1 remaining · 0 shortlisted
  - paragraph: Swipe right to shortlist · Swipe left to reject · Tap for details
  - heading "Bob SerpAPI" [level=3]
  - paragraph: Full Stack Engineer @ TechCo
  - paragraph: Remote
  - link "Open LinkedIn profile":
    - /url: https://www.linkedin.com/in/bob-serpapi/
    - text: LinkedIn
  - text: JavaScript React Node.js AWS
  - paragraph: Bob brings 5 years of experience in the field. The candidate has been serving in a Full Stack Engineer role. Recent experience includes TechCo. The candidate works out of Remote. They have strength in JavaScript, React, Node.js.
  - text: Shortlist / Reject
  - button "Reject"
  - button "Shortlist"
  - paragraph: Ready is the next recruiter handoff
  - paragraph: Save selected candidates and continue into Ready when you are satisfied with the shortlist. Ready then hands off into Results.
  - button "Move to Ready" [disabled]
  - text: Your selection helps us improve future matches
- alert
```

# Test source

```ts
  312 |       });
  313 |     });
  314 | 
  315 |     // Intercept any Eve-facing calls (should NOT be called from the browser)
  316 |     await page.route('**/eve/**', () => { eveCallMade = true; });
  317 |     await page.route('**/api/eve/**', () => { eveCallMade = true; });
  318 | 
  319 |     await page.goto('/review');
  320 |     await expect(page.getByText('Review Candidates')).toBeVisible({ timeout: 10_000 });
  321 |     await expect(page.getByText('Alice Internal')).toBeVisible();
  322 | 
  323 |     // Step 5: Click Interested
  324 |     await page.getByRole('button', { name: 'Interested' }).click();
  325 | 
  326 |     // Step 6: Verify network request was made
  327 |     expect(capturedRequest).not.toBeNull();
  328 |     expect(capturedRequest.method).toBe('POST');
  329 |     expect(capturedRequest.url).toContain('/candidates/internal-candidate-001/interest');
  330 |     expect(capturedRequest.url).toContain('jobId=job-1');
  331 | 
  332 |     // Step 7: Verify response succeeded
  333 |     expect(capturedResponse.success).toBe(true);
  334 |     expect(capturedResponse.data.status).toBe('PENDING');
  335 |     expect(capturedResponse.data.recruiter_action).toBe('INTERESTED');
  336 | 
  337 |     // Step 8: Verify Adam persistence fields in response
  338 |     expect(capturedResponse.data.request_id).toBeTruthy();
  339 |     expect(capturedResponse.data.candidate_id).toBe('internal-candidate-001');
  340 |     expect(capturedResponse.data.job_id).toBe('job-1');
  341 |     expect(capturedResponse.data.created_at).toBeTruthy();
  342 | 
  343 |     // Step 9: Verify Eve outbound event is queued (eve_delivery_status=queued)
  344 |     expect(capturedResponse.data.eve_delivery_status).toBe('queued');
  345 | 
  346 |     // Step 10: Eve is NOT directly called from the browser (Eve is called server-side)
  347 |     expect(eveCallMade).toBe(false);
  348 | 
  349 |     // UI feedback: interest request saved message should appear
  350 |     await expect(page.getByText(/interest request saved/i)).toBeVisible({ timeout: 5_000 });
  351 |   });
  352 | 
  353 |   test('internal candidate profile detail view shows richer DB-backed data', async ({ page }) => {
  354 |     await installFixtures(page, [INTERNAL_CANDIDATE]);
  355 | 
  356 |     // Mock the internal-profile endpoint
  357 |     await page.route('**/api/backend/candidates/internal-candidate-001/internal-profile**', (route) =>
  358 |       route.fulfill({
  359 |         status: 200,
  360 |         contentType: 'application/json',
  361 |         body: JSON.stringify({ success: true, data: INTERNAL_FULL_PROFILE, error: null }),
  362 |       })
  363 |     );
  364 | 
  365 |     await page.goto('/review');
  366 |     await expect(page.getByText('Review Candidates')).toBeVisible({ timeout: 10_000 });
  367 |     await expect(page.getByText('Alice Internal')).toBeVisible();
  368 | 
  369 |     // Open the candidate detail modal
  370 |     await page.getByRole('button', { name: /view full profile/i }).first().click();
  371 | 
  372 |     // Modal should open
  373 |     await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5_000 });
  374 | 
  375 |     // Verify richer DB-backed fields are shown
  376 |     await expect(page.getByRole('dialog').getByText('Alice Internal')).toBeVisible();
  377 |     await expect(page.getByRole('dialog').getByText(/Internal DB/i)).toBeVisible();
  378 | 
  379 |     // Internal profile section should be present
  380 |     await expect(page.getByRole('dialog').getByText(/Internal profile/i)).toBeVisible();
  381 | 
  382 |     // Work experience from DB
  383 |     await expect(page.getByRole('dialog').getByText(/Work experience/i)).toBeVisible();
  384 | 
  385 |     // Education from DB
  386 |     await expect(page.getByRole('dialog').getByText(/Education/i)).toBeVisible();
  387 | 
  388 |     // Skills from DB (richer set)
  389 |     await expect(page.getByRole('dialog').getByText('Docker')).toBeVisible();
  390 | 
  391 |     // Close modal
  392 |     await page.getByRole('button', { name: 'Close' }).click();
  393 |     await expect(page.getByRole('dialog')).toHaveCount(0);
  394 |   });
  395 | });
  396 | 
  397 | // ── SERPAPI CANDIDATE TESTS ───────────────────────────────────────────────────
  398 | 
  399 | test.describe('SERPAPI candidate review flow', () => {
  400 |   test('source is identified as serpapi and correct buttons are shown', async ({ page }) => {
  401 |     await installFixtures(page, [SERPAPI_CANDIDATE]);
  402 |     await page.goto('/review');
  403 | 
  404 |     await expect(page.getByText('Review Candidates')).toBeVisible({ timeout: 10_000 });
  405 |     await expect(page.getByText(/Swipe to shortlist/i)).toBeVisible({ timeout: 10_000 });
  406 | 
  407 |     // Candidate card is visible
  408 |     await expect(page.getByText('Bob SerpAPI')).toBeVisible();
  409 | 
  410 |     // Source badge shows "SerpAPI"
  411 |     const sourceBadge = page.locator('span').filter({ hasText: /^SerpAPI$/ }).first();
> 412 |     await expect(sourceBadge).toBeVisible();
      |                               ^ Error: expect(locator).toBeVisible() failed
  413 | 
  414 |     // Correct buttons — Shortlist and Reject MUST be visible
  415 |     await expect(page.getByRole('button', { name: 'Shortlist' })).toBeVisible();
  416 |     await expect(page.getByRole('button', { name: 'Reject' })).toBeVisible();
  417 | 
  418 |     // Interested and Not Interested must NOT be visible
  419 |     await expect(page.getByRole('button', { name: 'Interested' })).toHaveCount(0);
  420 |     await expect(page.getByRole('button', { name: 'Not Interested' })).toHaveCount(0);
  421 |   });
  422 | 
  423 |   test('Shortlist fires the SerpAPI select flow and no Eve interest request is generated', async ({ page }) => {
  424 |     let selectCallMade = false;
  425 |     let interestCallMade = false;
  426 |     let capturedSelectRequest = null;
  427 |     let capturedSelectResponse = null;
  428 | 
  429 |     await installFixtures(page, [SERPAPI_CANDIDATE]);
  430 | 
  431 |     // Intercept the SerpAPI shortlist endpoint (POST /candidates/select)
  432 |     await page.route('**/api/backend/candidates/select', async (route) => {
  433 |       selectCallMade = true;
  434 |       capturedSelectRequest = {
  435 |         method: route.request().method(),
  436 |         url: route.request().url(),
  437 |         body: route.request().postDataJSON(),
  438 |       };
  439 |       const responseBody = {
  440 |         success: true,
  441 |         data: {
  442 |           jobId: 'job-1',
  443 |           candidateId: 'serpapi-candidate-001',
  444 |           status: 'selected',
  445 |           enrichmentStatus: 'enrichment_pending',
  446 |           outreachStatus: 'queued',
  447 |         },
  448 |         error: null,
  449 |       };
  450 |       capturedSelectResponse = responseBody;
  451 |       await route.fulfill({
  452 |         status: 200,
  453 |         contentType: 'application/json',
  454 |         body: JSON.stringify(responseBody),
  455 |       });
  456 |     });
  457 | 
  458 |     // Intercept the interest endpoint — must NOT be called for SerpAPI candidates
  459 |     await page.route('**/api/backend/candidates/serpapi-candidate-001/interest**', async (route) => {
  460 |       interestCallMade = true;
  461 |       await route.fulfill({
  462 |         status: 200,
  463 |         contentType: 'application/json',
  464 |         body: JSON.stringify({ success: true, data: {}, error: null }),
  465 |       });
  466 |     });
  467 | 
  468 |     await page.goto('/review');
  469 |     await expect(page.getByText('Review Candidates')).toBeVisible({ timeout: 10_000 });
  470 |     await expect(page.getByText('Bob SerpAPI')).toBeVisible();
  471 | 
  472 |     // Click Shortlist
  473 |     await page.getByRole('button', { name: 'Shortlist' }).click();
  474 | 
  475 |     // Verify the SerpAPI select flow was triggered
  476 |     expect(selectCallMade).toBe(true);
  477 |     expect(capturedSelectRequest.method).toBe('POST');
  478 |     expect(capturedSelectRequest.url).toContain('/candidates/select');
  479 |     expect(capturedSelectRequest.body.candidateId).toBe('serpapi-candidate-001');
  480 |     expect(capturedSelectRequest.body.jobId).toBe('job-1');
  481 | 
  482 |     // Verify response succeeded
  483 |     expect(capturedSelectResponse.success).toBe(true);
  484 | 
  485 |     // Verify no Eve recruiter-interest request was generated
  486 |     expect(interestCallMade).toBe(false);
  487 |   });
  488 | 
  489 |   test('Reject fires swipe endpoint and no Eve interest request is generated', async ({ page }) => {
  490 |     let swipeCallMade = false;
  491 |     let interestCallMade = false;
  492 |     let capturedSwipeRequest = null;
  493 | 
  494 |     await installFixtures(page, [SERPAPI_CANDIDATE]);
  495 | 
  496 |     await page.route('**/api/backend/candidates/swipe', async (route) => {
  497 |       swipeCallMade = true;
  498 |       capturedSwipeRequest = {
  499 |         method: route.request().method(),
  500 |         body: route.request().postDataJSON(),
  501 |       };
  502 |       await route.fulfill({
  503 |         status: 200,
  504 |         contentType: 'application/json',
  505 |         body: JSON.stringify({
  506 |           success: true,
  507 |           data: {
  508 |             jobId: 'job-1',
  509 |             candidateId: 'serpapi-candidate-001',
  510 |             action: 'reject',
  511 |             previousState: 'new',
  512 |             newState: 'rejected',
```