# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: candidate-review-flow.spec.mjs >> INTERNAL candidate review flow >> source is identified as internal and correct buttons are shown
- Location: e2e\candidate-review-flow.spec.mjs:253:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('span').filter({ hasText: /^Internal$/ }).first()
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for locator('span').filter({ hasText: /^Internal$/ }).first()

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
  - paragraph: Swipe to interested
  - paragraph: 1 remaining · 0 shortlisted
  - paragraph: Swipe right to interested · Swipe left to not interested · Tap for details
  - heading "Alice Internal" [level=3]
  - paragraph: Senior Backend Engineer @ Acme Corp
  - paragraph: Bengaluru
  - link "Open LinkedIn profile":
    - /url: https://www.linkedin.com/in/alice-internal/
    - text: LinkedIn
  - text: Python FastAPI PostgreSQL Redis
  - paragraph: Alice brings 7 years of experience in the field. The candidate comes with a background as a Senior Backend Engineer. The candidate has experience at Acme Corp. The candidate is from Bengaluru. They have strength in Python, FastAPI, PostgreSQL.
  - text: Interested / Not Interested
  - button "Not Interested"
  - button "Interested"
  - paragraph: Ready is the next recruiter handoff
  - paragraph: Save selected candidates and continue into Ready when you are satisfied with the shortlist. Ready then hands off into Results.
  - button "Move to Ready" [disabled]
  - text: Your selection helps us improve future matches
- alert
```

# Test source

```ts
  166 |     route.fulfill({
  167 |       status: 200,
  168 |       contentType: 'application/json',
  169 |       body: JSON.stringify({
  170 |         success: true,
  171 |         data: { user: { id: 'user-1', email: 'recruiter@example.com', role: 'admin', name: 'Recruiter' } },
  172 |         error: null,
  173 |       }),
  174 |     })
  175 |   );
  176 | 
  177 |   await page.route('**/api/backend/auth/csrf', (route) =>
  178 |     route.fulfill({
  179 |       status: 200,
  180 |       contentType: 'application/json',
  181 |       body: JSON.stringify({ success: true, data: { token: 'test-csrf' }, error: null }),
  182 |     })
  183 |   );
  184 | 
  185 |   await page.route('**/api/backend/recruiters/user-1/intelligence/jobs/job-1', (route) =>
  186 |     route.fulfill({
  187 |       status: 200,
  188 |       contentType: 'application/json',
  189 |       body: JSON.stringify({
  190 |         success: true,
  191 |         data: {
  192 |           calibration: {
  193 |             stage: 'real_sourcing_ready',
  194 |             current_round_index: 3,
  195 |             archetype_sets: [],
  196 |             current_pair: null,
  197 |             voice_summary: 'Looking for a senior backend engineer with strong API design and PostgreSQL experience.',
  198 |           },
  199 |           interview: {
  200 |             voice_summary: 'Looking for a senior backend engineer with strong API design and PostgreSQL experience.',
  201 |           },
  202 |         },
  203 |         error: null,
  204 |       }),
  205 |     })
  206 |   );
  207 | 
  208 |   await page.route('**/api/backend/candidates?**', async (route) => {
  209 |     const url = new URL(route.request().url());
  210 |     if (url.pathname !== '/api/backend/candidates') {
  211 |       await route.continue();
  212 |       return;
  213 |     }
  214 |     await route.fulfill({
  215 |       status: 200,
  216 |       contentType: 'application/json',
  217 |       body: JSON.stringify({ success: true, data: candidates, error: null }),
  218 |     });
  219 |   });
  220 | 
  221 |   await page.route('**/api/backend/candidates/accepted**', (route) =>
  222 |     route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data: [], error: null }) })
  223 |   );
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
> 266 |     await expect(sourceBadge).toBeVisible();
      |                               ^ Error: expect(locator).toBeVisible() failed
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
```