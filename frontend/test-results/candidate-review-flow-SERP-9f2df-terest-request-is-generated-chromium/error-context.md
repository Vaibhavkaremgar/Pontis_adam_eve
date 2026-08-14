# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: candidate-review-flow.spec.mjs >> SERPAPI candidate review flow >> Shortlist fires the SerpAPI select flow and no Eve interest request is generated
- Location: e2e\candidate-review-flow.spec.mjs:423:3

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: true
Received: false
```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - button "Open Next.js Dev Tools" [ref=e7] [cursor=pointer]:
    - img [ref=e8]
  - alert [ref=e11]
  - generic [ref=e13]:
    - generic [ref=e15]:
      - generic [ref=e16]:
        - generic [ref=e17]:
          - generic [ref=e18]:
            - generic [ref=e19]: AS
            - generic [ref=e20]:
              - paragraph [ref=e21]: Adam Super Admin
              - paragraph [ref=e22]: Signed in as super admin
          - heading "Dashboard" [level=1] [ref=e23]
          - paragraph [ref=e24]: Monitor agencies, users, jobs, and candidates from a single super-admin control surface.
        - button "Log out" [ref=e25] [cursor=pointer]:
          - img [ref=e26]
          - text: Log out
      - generic [ref=e30]:
        - button "Dashboard" [ref=e31] [cursor=pointer]:
          - img [ref=e32]
          - text: Dashboard
        - button "Agencies" [ref=e37] [cursor=pointer]:
          - img [ref=e38]
          - text: Agencies
        - button "Users" [ref=e42] [cursor=pointer]:
          - img [ref=e43]
          - text: Users
    - generic [ref=e48]:
      - generic [ref=e50]:
        - paragraph [ref=e51]: Total Agencies
        - heading "0" [level=3] [ref=e52]
      - generic [ref=e54]:
        - paragraph [ref=e55]: Total Users
        - heading "0" [level=3] [ref=e56]
      - generic [ref=e58]:
        - paragraph [ref=e59]: Active Users
        - heading "0" [level=3] [ref=e60]
      - generic [ref=e62]:
        - paragraph [ref=e63]: Inactive Users
        - heading "0" [level=3] [ref=e64]
      - generic [ref=e66]:
        - paragraph [ref=e67]: Total Jobs
        - heading "0" [level=3] [ref=e68]
      - generic [ref=e70]:
        - paragraph [ref=e71]: Total Candidates
        - heading "0" [level=3] [ref=e72]
    - generic [ref=e73]:
      - generic [ref=e75]:
        - generic [ref=e76]:
          - heading "Recent Agencies" [level=3] [ref=e77]
          - paragraph [ref=e78]: Latest agencies created in Adam.
        - button "View all" [ref=e79] [cursor=pointer]
      - generic [ref=e81]:
        - generic [ref=e82]:
          - heading "Recent Users" [level=3] [ref=e83]
          - paragraph [ref=e84]: Latest users created across agencies.
        - button "View all" [ref=e85] [cursor=pointer]
```

# Test source

```ts
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
  471 | 
  472 |     // Click Shortlist
  473 |     await page.getByRole('button', { name: 'Shortlist' }).click();
  474 | 
  475 |     // Verify the SerpAPI select flow was triggered
> 476 |     expect(selectCallMade).toBe(true);
      |                            ^ Error: expect(received).toBe(expected) // Object.is equality
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
  513 |             message: 'ok',
  514 |           },
  515 |           error: null,
  516 |         }),
  517 |       });
  518 |     });
  519 | 
  520 |     await page.route('**/api/backend/candidates/serpapi-candidate-001/interest**', async (route) => {
  521 |       interestCallMade = true;
  522 |       await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: {}, error: null }) });
  523 |     });
  524 | 
  525 |     await page.goto('/review');
  526 |     await expect(page.getByText('Review Candidates')).toBeVisible({ timeout: 10_000 });
  527 |     await expect(page.getByText('Bob SerpAPI')).toBeVisible();
  528 | 
  529 |     // Click Reject
  530 |     await page.getByRole('button', { name: 'Reject' }).click();
  531 | 
  532 |     // Verify swipe endpoint was called with reject action
  533 |     expect(swipeCallMade).toBe(true);
  534 |     expect(capturedSwipeRequest.method).toBe('POST');
  535 |     expect(capturedSwipeRequest.body.action).toBe('reject');
  536 |     expect(capturedSwipeRequest.body.candidateId).toBe('serpapi-candidate-001');
  537 | 
  538 |     // Confirm no Eve recruiter-interest request was generated
  539 |     expect(interestCallMade).toBe(false);
  540 |   });
  541 | });
  542 | 
```