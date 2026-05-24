# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: hiring-outreach.spec.mjs >> review shortlist carries into outreach and queues the selected candidates
- Location: e2e\hiring-outreach.spec.mjs:102:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText('Outreach sent to 1 candidate.')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText('Outreach sent to 1 candidate.')
    - waiting for" http://127.0.0.1:3000/review" navigation to finish...
    - navigated to "http://127.0.0.1:3000/review"

```

```yaml
- banner:
  - link "Back to Voice Intake":
    - /url: /voice
  - link "P Pontis":
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
- paragraph: Outreach
- text: "6"
- paragraph: Ready
- main:
  - heading "Review Candidates" [level=1]
  - paragraph: A refined shortlist review built for fast, confident selection.
  - text: "Progress: 0 / 3 Selected: 0 Rejected: 0 Preparing the next batch. If this screen just refreshed, the session will resume from the last saved step. Your selection helps us improve future matches"
```

# Test source

```ts
  542 |             toEmail: `${candidateId}@example.com`,
  543 |             attemptCount: 1,
  544 |             lastSentAt: statusesByCandidate[candidateId] === 'sent' ? new Date().toISOString() : null,
  545 |             nextFollowUpAt: null,
  546 |             lastError: '',
  547 |           })),
  548 |           error: null,
  549 |         }),
  550 |       });
  551 |       return;
  552 |     }
  553 | 
  554 |     if (pathname === '/api/backend/interviews' && method === 'GET') {
  555 |       await route.fulfill({
  556 |         status: 200,
  557 |         contentType: 'application/json',
  558 |         body: JSON.stringify({
  559 |           success: true,
  560 |           data: outreachIds.map((candidateId) => ({
  561 |             candidateId,
  562 |             name: `Candidate ${candidateId.slice(-1)}`,
  563 |             status: statusesByCandidate[candidateId] === 'sent' ? 'contacted' : 'shortlisted',
  564 |           })),
  565 |           error: null,
  566 |         }),
  567 |       });
  568 |       return;
  569 |     }
  570 | 
  571 |     if (pathname === '/api/backend/metrics' && method === 'GET') {
  572 |       await route.fulfill({
  573 |         status: 200,
  574 |         contentType: 'application/json',
  575 |         body: JSON.stringify({
  576 |           success: true,
  577 |           data: {
  578 |             emails_sent: outreachIds.length,
  579 |             replies_received: 0,
  580 |             interviews_booked: 0,
  581 |             conversion_rate: 0,
  582 |           },
  583 |           error: null,
  584 |         }),
  585 |       });
  586 |       return;
  587 |     }
  588 | 
  589 |     await route.fulfill({
  590 |       status: 404,
  591 |       contentType: 'application/json',
  592 |       body: JSON.stringify({
  593 |         success: false,
  594 |         data: null,
  595 |         error: `Unhandled test route: ${pathname}`,
  596 |       }),
  597 |     });
  598 |   });
  599 |   }
  600 | 
  601 |   await page.goto('/review');
  602 |   await expect(page.getByText('Review Candidates')).toBeVisible();
  603 | 
  604 |   await expect(page.getByText('Preference calibration 1 / 3')).toBeVisible();
  605 |   await expect(page.getByTestId('calibration-select-archetype-startup-builder')).toBeVisible();
  606 |   await page.getByTestId('calibration-select-archetype-startup-builder').click();
  607 |   await expect(page.getByText('Preference calibration 2 / 3')).toBeVisible();
  608 |   await page.getByTestId('calibration-select-archetype-operator').click();
  609 |   await expect(page.getByText('Preference calibration 3 / 3')).toBeVisible();
  610 |   await page.getByTestId('calibration-select-archetype-ai-infra').click();
  611 | 
  612 |   const selectCurrentBatchCandidate = async () => {
  613 |     const candidateId = ['candidate-1', 'candidate-3', 'candidate-5'][selectionStep];
  614 |     await page.getByTestId(`batch-select-${candidateId}`).click();
  615 |   };
  616 | 
  617 |   await expect(page.getByText('Batch 1 of 3')).toBeVisible();
  618 |   await selectCurrentBatchCandidate();
  619 | 
  620 |   await expect(page.getByText('Batch 2 of 3')).toBeVisible();
  621 |   await selectCurrentBatchCandidate();
  622 | 
  623 |   await expect(page.getByText('Batch 3 of 3')).toBeVisible();
  624 |   await selectCurrentBatchCandidate();
  625 | 
  626 |   await expect(page.getByText('Selection complete')).toBeVisible();
  627 | 
  628 |   const selectFinalCandidate = async (candidateName) => {
  629 |     const candidateId =
  630 |       candidateName === 'Candidate 2'
  631 |         ? 'candidate-2'
  632 |         : candidateName === 'Candidate 4'
  633 |           ? 'candidate-4'
  634 |           : 'candidate-6';
  635 |     await page.getByTestId(`review-select-${candidateId}`).click();
  636 |   };
  637 | 
  638 |   await selectFinalCandidate('Candidate 2');
  639 |   await selectFinalCandidate('Candidate 4');
  640 |   await selectFinalCandidate('Candidate 6');
  641 | 
> 642 |   await expect(page.getByText('Outreach sent to 1 candidate.')).toBeVisible();
      |                                                                 ^ Error: expect(locator).toBeVisible() failed
  643 |   await expect(page.getByTestId('continue-to-outreach')).toBeVisible();
  644 |   await page.getByTestId('continue-to-outreach').click();
  645 | 
  646 |   await expect(page).toHaveURL(/\/ready/);
  647 |   await expect(page.getByText('Candidates ready for interview')).toBeVisible();
  648 |   await expect(page.getByText('3 records')).toBeVisible();
  649 |   await expect(page.getByText('candidate-2@example.com')).toBeVisible();
  650 |   await expect(page.getByText('candidate-4@example.com')).toBeVisible();
  651 |   await expect(page.getByText('candidate-6@example.com')).toBeVisible();
  652 | 
  653 |   expect(swipeCalls).toHaveLength(3);
  654 |   expect(swipeCalls.map((item) => item.candidateId).sort()).toEqual(outreachIds.slice().sort());
  655 |   expect(sentOutreachPayloads).toHaveLength(3);
  656 |   expect(sentOutreachPayloads.map((item) => item.selectedCandidates[0]).sort()).toEqual(outreachIds.slice().sort());
  657 |   expect(sentOutreachPayloads.every((item) => !item.customBody)).toBe(true);
  658 | });
  659 | 
```