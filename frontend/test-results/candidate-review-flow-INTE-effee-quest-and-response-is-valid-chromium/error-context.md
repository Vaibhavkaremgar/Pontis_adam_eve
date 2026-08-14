# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: candidate-review-flow.spec.mjs >> INTERNAL candidate review flow >> Interested button fires correct API request and response is valid
- Location: e2e\candidate-review-flow.spec.mjs:277:3

# Error details

```
Error: locator.click: Error: strict mode violation: getByRole('button', { name: 'Interested' }) resolved to 2 elements:
    1) <button type="button" class="flex h-14 flex-1 items-center justify-center gap-2 rounded-[16px] border-2 border-[#FCA5A5] bg-white font-body text-[15px] font-semibold text-[#DC2626] transition hover:bg-[#FEF2F2] disabled:opacity-50">…</button> aka getByRole('button', { name: 'Not Interested' })
    2) <button type="button" class="flex h-14 flex-1 items-center justify-center gap-2 rounded-[16px] bg-[#0F6B3A] font-body text-[15px] font-semibold text-white shadow-[0_6px_16px_rgba(15,107,58,0.22)] transition hover:bg-[#0C5A31] disabled:opacity-50">…</button> aka getByRole('button', { name: 'Interested', exact: true })

Call log:
  - waiting for getByRole('button', { name: 'Interested' })

```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - generic [ref=e2]:
    - banner [ref=e3]:
      - generic [ref=e4]:
        - generic [ref=e5]:
          - link "Back to Voice Intake" [ref=e6] [cursor=pointer]:
            - /url: /voice
            - img [ref=e7]
            - generic [ref=e9]: Back to Voice Intake
          - link "Pontis" [ref=e10] [cursor=pointer]:
            - /url: /company
            - generic [ref=e11]: Pontis
        - button "Sign out" [ref=e12] [cursor=pointer]:
          - img [ref=e13]
          - generic [ref=e16]: Sign out
    - generic [ref=e19]:
      - generic [ref=e21]:
        - generic [ref=e22]: "1"
        - paragraph [ref=e23]: Company
      - generic [ref=e24]:
        - generic [ref=e25]: "2"
        - paragraph [ref=e26]: Job
      - generic [ref=e27]:
        - generic [ref=e28]: "3"
        - paragraph [ref=e29]: Voice
      - generic [ref=e30]:
        - generic [ref=e31]: "4"
        - paragraph [ref=e32]: Review
      - generic [ref=e33]:
        - generic [ref=e34]: "5"
        - paragraph [ref=e35]: Ready
      - generic [ref=e36]:
        - generic [ref=e37]: "6"
        - paragraph [ref=e38]: Results
    - main [ref=e39]:
      - generic [ref=e41]:
        - generic [ref=e43]:
          - heading "Review Candidates" [level=1] [ref=e44]
          - paragraph [ref=e45]: A refined shortlist review built for fast, confident selection.
        - generic [ref=e46]:
          - button "Voice intake summary" [ref=e48] [cursor=pointer]
          - generic [ref=e50]:
            - paragraph [ref=e51]: Candidate review
            - paragraph [ref=e52]: For a better understanding of each candidate and their work, please open the LinkedIn profile from the LinkedIn button on the card before making your choice.
          - generic [ref=e53]:
            - generic [ref=e55]:
              - paragraph [ref=e56]: Swipe to interested
              - paragraph [ref=e57]: 1 remaining · 0 shortlisted
            - paragraph [ref=e58]: Swipe right to interested · Swipe left to not interested · Tap for details
            - generic [ref=e61]:
              - generic [ref=e62]:
                - generic [ref=e63]:
                  - heading "Alice Internal" [level=3] [ref=e64]
                  - paragraph [ref=e65]: Senior Backend Engineer @ Acme Corp
                  - paragraph [ref=e66]:
                    - img [ref=e67]
                    - text: Bengaluru
                - link "Open LinkedIn profile" [ref=e71] [cursor=pointer]:
                  - /url: https://www.linkedin.com/in/alice-internal/
                  - text: LinkedIn
                  - img [ref=e72]
              - generic [ref=e75]:
                - generic [ref=e76]: Python
                - generic [ref=e77]: FastAPI
                - generic [ref=e78]: PostgreSQL
                - generic [ref=e79]: Redis
              - paragraph [ref=e81]: Alice brings 7 years of experience in the field. The candidate comes with a background as a Senior Backend Engineer. The candidate has experience at Acme Corp. The candidate is from Bengaluru. They have strength in Python, FastAPI, PostgreSQL.
              - generic [ref=e82]: Interested / Not Interested
              - generic [ref=e83]:
                - button "Not Interested" [ref=e84] [cursor=pointer]:
                  - img [ref=e85]
                  - text: Not Interested
                - button "Interested" [ref=e89] [cursor=pointer]:
                  - img [ref=e90]
                  - text: Interested
        - generic [ref=e97]:
          - generic [ref=e98]:
            - paragraph [ref=e99]: Ready is the next recruiter handoff
            - paragraph [ref=e100]: Save selected candidates and continue into Ready when you are satisfied with the shortlist. Ready then hands off into Results.
          - button "Move to Ready" [disabled]
        - generic [ref=e101]:
          - img [ref=e102]
          - generic [ref=e105]: Your selection helps us improve future matches
  - button "Open Next.js Dev Tools" [ref=e111] [cursor=pointer]:
    - generic [ref=e114]:
      - text: Rendering
      - generic [ref=e115]:
        - generic [ref=e116]: .
        - generic [ref=e117]: .
        - generic [ref=e118]: .
  - alert [ref=e119]
```

# Test source

```ts
  224 | 
  225 |   await page.route('**/api/backend/candidates/pending-acceptance**', (route) =>
  226 |     route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: [], error: null }) })
  227 |   );
  228 | 
  229 |   await page.route('**/api/backend/interviews**', (route) =>
  230 |     route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: [], error: null }) })
  231 |   );
  232 | 
  233 |   await page.route('**/api/backend/ats/timeline**', (route) =>
  234 |     route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: [], error: null }) })
  235 |   );
  236 | 
  237 |   await page.route('**/api/backend/ats/notifications**', (route) =>
  238 |     route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: [], error: null }) })
  239 |   );
  240 | 
  241 |   await page.context().route('**://www.linkedin.com/**', (route) =>
  242 |     route.fulfill({
  243 |       status: 200,
  244 |       contentType: 'text/html',
  245 |       body: '<html><body>LinkedIn stub</body></html>',
  246 |     })
  247 |   );
  248 | }
  249 | 
  250 | // ── INTERNAL CANDIDATE TESTS ──────────────────────────────────────────────────
  251 | 
  252 | test.describe('INTERNAL candidate review flow', () => {
  253 |   test('source is identified as internal and correct buttons are shown', async ({ page }) => {
  254 |     await installFixtures(page, [INTERNAL_CANDIDATE]);
  255 |     await page.goto('/review');
  256 | 
  257 |     // Step 1: Page loads
  258 |     await expect(page.getByText('Review Candidates')).toBeVisible({ timeout: 10_000 });
  259 |     await expect(page.getByText(/Swipe to interested/i)).toBeVisible({ timeout: 10_000 });
  260 | 
  261 |     // Step 2: Candidate card is visible
  262 |     await expect(page.getByText('Alice Internal')).toBeVisible();
  263 | 
  264 |     // Step 3: Source badge shows "Internal"
  265 |     const sourceBadge = page.locator('span').filter({ hasText: /^Internal$/ }).first();
  266 |     await expect(sourceBadge).toBeVisible();
  267 | 
  268 |     // Step 4: Correct buttons — Interested and Not Interested MUST be visible
  269 |     await expect(page.getByRole('button', { name: 'Interested' })).toBeVisible();
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
> 324 |     await page.getByRole('button', { name: 'Interested' }).click();
      |                                                            ^ Error: locator.click: Error: strict mode violation: getByRole('button', { name: 'Interested' }) resolved to 2 elements:
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
```