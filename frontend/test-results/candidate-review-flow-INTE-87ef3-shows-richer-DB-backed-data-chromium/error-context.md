# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: candidate-review-flow.spec.mjs >> INTERNAL candidate review flow >> internal candidate profile detail view shows richer DB-backed data
- Location: e2e\candidate-review-flow.spec.mjs:353:3

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: locator.click: Test timeout of 30000ms exceeded.
Call log:
  - waiting for getByRole('button', { name: /view full profile/i }).first()
    - waiting for" http://127.0.0.1:3000/admin" navigation to finish...
    - navigated to "http://127.0.0.1:3000/admin"

```

# Page snapshot

```yaml
- generic [ref=e2]: Loading admin access...
```

# Test source

```ts
  270 |     await expect(page.getByRole('button', { name: 'Not Interested' })).toBeVisible();
  271 | 
  272 |     // Step 5: Shortlist and Reject must NOT be visible
  273 |     await expect(page.getByRole('button', { name: 'Shortlist' })).toHaveCount(0);
  274 |     await expect(page.getByRole('button', { name: 'Reject' })).toHaveCount(0);
  275 |   });
  276 | 
  277 |   test('Interested button fires correct API request and response is valid', async ({ page }) => {
  278 |     let capturedRequest = null;
  279 |     let capturedResponse = null;
  280 |     let eveCallMade = false;
  281 | 
  282 |     await installFixtures(page, [INTERNAL_CANDIDATE]);
  283 | 
  284 |     // Intercept the interest endpoint — capture request + return mock response
  285 |     await page.route('**/api/backend/candidates/internal-candidate-001/interest**', async (route) => {
  286 |       capturedRequest = {
  287 |         method: route.request().method(),
  288 |         url: route.request().url(),
  289 |         // Do NOT log auth headers — only log safe metadata
  290 |         hasAuthHeader: Boolean(route.request().headers()['cookie'] || route.request().headers()['authorization']),
  291 |       };
  292 |       const responseBody = {
  293 |         success: true,
  294 |         data: {
  295 |           request_id: 'req-internal-001',
  296 |           candidate_id: 'internal-candidate-001',
  297 |           job_id: 'job-1',
  298 |           status: 'PENDING',
  299 |           recruiter_action: 'INTERESTED',
  300 |           created_at: new Date().toISOString(),
  301 |           updated_at: new Date().toISOString(),
  302 |           responded_at: null,
  303 |           eve_delivery_status: 'queued',
  304 |         },
  305 |         error: null,
  306 |       };
  307 |       capturedResponse = responseBody;
  308 |       await route.fulfill({
  309 |         status: 200,
  310 |         contentType: 'application/json',
  311 |         body: JSON.stringify(responseBody),
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
> 370 |     await page.getByRole('button', { name: /view full profile/i }).first().click();
      |                                                                            ^ Error: locator.click: Test timeout of 30000ms exceeded.
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
  412 |     await expect(sourceBadge).toBeVisible();
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
```