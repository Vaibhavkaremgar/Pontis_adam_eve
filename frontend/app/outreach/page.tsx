"use client";

/**
 * What this file does:
 * Lets recruiter select candidates, preview and edit the outreach email, then send.
 * Fetches shortlisted candidates server-side — does NOT rely on frontend state.
 *
 * What API it connects to:
 * GET /candidates/shortlisted  — server-side shortlisted-only list
 * GET /outreach/preview        — fetches auto-generated subject + body
 * POST /outreach               — sends outreach with optional recruiter-edited body
 */
import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { useAppContext } from "@/context/AppContext";
import { getShortlistedCandidates } from "@/lib/api/candidates";
import { getEmailPreview, getOutreachStatuses, queueOutreach, type OutreachStatusItem } from "@/lib/api/outreach";
import type { Candidate } from "@/types";

function OutreachContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, isSessionReady, jobId, isRefined } = useAppContext();
  const skipVoice = searchParams.get("skipVoice") === "1" || searchParams.get("skipVoice") === "true";

  const [shortlisted, setShortlisted] = useState<Candidate[]>([]);
  const [isLoadingCandidates, setIsLoadingCandidates] = useState(false);
  const [selectedCandidates, setSelectedCandidates] = useState<string[]>([]);
  const [emailBody, setEmailBody] = useState("");
  const [emailSubject, setEmailSubject] = useState("");
  const [previewToEmail, setPreviewToEmail] = useState("");
  const [usingFallbackEmail, setUsingFallbackEmail] = useState(false);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isOutreachComplete, setIsOutreachComplete] = useState(false);
  const [outreachStatuses, setOutreachStatuses] = useState<OutreachStatusItem[]>([]);

  // Auth + flow guard
  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) { router.replace("/login"); return; }
    if (!jobId) { router.replace("/job"); return; }
    if (!isRefined && !skipVoice) {
      router.replace(`/voice?jobId=${encodeURIComponent(jobId)}`);
      return;
    }
  }, [isRefined, isSessionReady, jobId, router, skipVoice, user]);

  // Fetch shortlisted candidates server-side on mount
  useEffect(() => {
    if (!isSessionReady || !user || !jobId) return;
    setIsLoadingCandidates(true);
    getShortlistedCandidates(jobId).then((result) => {
      if (result.success && result.data) setShortlisted(result.data);
      setIsLoadingCandidates(false);
    });
  }, [isSessionReady, user, jobId]);

  useEffect(() => {
    if (!jobId || !isOutreachComplete) return;
    let cancelled = false;
    const refresh = async () => {
      const result = await getOutreachStatuses(jobId);
      if (!cancelled && result.success && result.data) {
        setOutreachStatuses(result.data);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [isOutreachComplete, jobId]);

  // When selection changes to exactly one candidate, fetch the real preview from backend.
  useEffect(() => {
    if (!jobId) return;

    if (selectedCandidates.length === 0) {
      setEmailSubject("");
      setEmailBody("");
      setPreviewToEmail("");
      return;
    }

    if (selectedCandidates.length === 1) {
      setIsLoadingPreview(true);
      getEmailPreview(jobId, selectedCandidates[0]).then((result) => {
        if (result.success && result.data) {
          setEmailSubject(result.data.subject);
          setEmailBody(result.data.body);
          setPreviewToEmail(result.data.toEmail);
          setUsingFallbackEmail((result.data as any).usingFallbackEmail ?? false);
        }
        setIsLoadingPreview(false);
      });
      return;
    }

    setEmailSubject("Personalised email per candidate");
    setEmailBody(
      "Each candidate will receive a personalised email generated from their profile and the job details.\n\n" +
      "Select a single candidate to preview and edit their specific email before sending."
    );
    setPreviewToEmail("");
  }, [selectedCandidates, jobId]);

  const canSubmit = useMemo(
    () => selectedCandidates.length > 0 && !isSubmitting,
    [isSubmitting, selectedCandidates]
  );

  const toggleCandidate = (candidateId: string) => {
    setSelectedCandidates((prev) =>
      prev.includes(candidateId) ? prev.filter((id) => id !== candidateId) : [...prev, candidateId]
    );
  };

  const handleSendOutreach = async () => {
    if (!canSubmit) return;
    setIsSubmitting(true);
    setError("");
    setFeedback("");
    setIsOutreachComplete(false);

    const customBody = selectedCandidates.length === 1 ? emailBody.trim() : "";
    const result = await queueOutreach({ jobId, selectedCandidates, customBody });
    if (!result.success || !result.data) {
      setError(result.error || "Failed to send outreach.");
      setIsSubmitting(false);
      return;
    }

    setFeedback(`Outreach queued for ${result.data.selected_count} candidate${result.data.selected_count !== 1 ? "s" : ""}.`);
    setIsOutreachComplete(true);
    setIsSubmitting(false);

    // Refresh shortlisted list to reflect contacted status
    if (jobId) {
      getShortlistedCandidates(jobId).then((r) => {
        if (r.success && r.data) setShortlisted(r.data);
      });
    }
  };

  return (
    <AppShell activeStep={5}>
      <Card className="mx-auto w-full max-w-[560px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Candidate Outreach</CardTitle>
          <CardDescription>Select candidates, review the email, then send</CardDescription>
        </CardHeader>

        <CardContent className="space-y-6">
          {isRefined && (
            <div className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
              Refined based on your input
            </div>
          )}
          {!isRefined && skipVoice && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              Skipping voice intake and going straight to outreach.
            </div>
          )}

          {/* Candidate selection */}
          <div className="space-y-3">
            {isLoadingCandidates && (
              <p className="text-sm text-gray-500">Loading shortlisted candidates…</p>
            )}
            {!isLoadingCandidates && shortlisted.map((candidate) => (
              <label
                key={candidate.id}
                className="flex cursor-pointer items-start justify-between rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] p-4"
              >
                <div className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    className="mt-1 h-4 w-4"
                    checked={selectedCandidates.includes(candidate.id)}
                    onChange={() => toggleCandidate(candidate.id)}
                    disabled={isSubmitting}
                  />
                  <div className="space-y-0.5">
                    <p className="font-semibold text-gray-900">{candidate.name || candidate.id.slice(0, 8)}</p>
                    <p className="text-sm text-gray-600">{candidate.role}{candidate.company ? ` @ ${candidate.company}` : ""}</p>
                  </div>
                </div>
                <Badge variant="medium">{candidate.status}</Badge>
              </label>
            ))}
            {!isLoadingCandidates && shortlisted.length === 0 && (
              <div className="space-y-3">
                <div className="rounded-xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4 text-sm text-gray-600">
                  No shortlisted candidates yet. Go back to Review and accept candidates first.
                </div>
                <Button variant="outline" className="w-full justify-center" onClick={() => router.push("/review")}>
                  ← Back to Review
                </Button>
              </div>
            )}
          </div>

          <Separator />

          {/* Email preview */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold text-gray-900">Email Preview</p>
              {selectedCandidates.length === 1 && previewToEmail && (
                <p className="text-xs text-gray-500">
                  To: {previewToEmail}
                  {usingFallbackEmail && (
                    <span className="ml-2 text-amber-600">(no email on file)</span>
                  )}
                </p>
              )}
            </div>

            {selectedCandidates.length === 0 && (
              <div className="rounded-xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4 text-sm text-gray-500">
                Select a candidate above to preview their outreach email.
              </div>
            )}

            {selectedCandidates.length > 0 && (
              <>
                <div className="rounded-lg border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] px-3 py-2 text-xs text-gray-500">
                  <span className="font-medium text-gray-700">Subject: </span>
                  {isLoadingPreview ? "Loading..." : emailSubject}
                </div>
                <Textarea
                  className="min-h-[200px] text-sm text-gray-800 leading-relaxed"
                  value={isLoadingPreview ? "Loading preview..." : emailBody}
                  onChange={(e) => setEmailBody(e.target.value)}
                  disabled={isLoadingPreview || isSubmitting || selectedCandidates.length > 1}
                  placeholder="Email body will appear here once you select a candidate."
                />
                {selectedCandidates.length === 1 && (
                  <p className="text-xs text-gray-400">
                    You can edit this email before sending. Changes apply only to this send.
                  </p>
                )}
              </>
            )}
          </div>

          <Button
            className="w-full justify-center"
            onClick={handleSendOutreach}
            disabled={!canSubmit || shortlisted.length === 0}
          >
            {isSubmitting ? "Sending..." : "Send Outreach"}
          </Button>

          {isOutreachComplete && (
            <div className="rounded-xl border border-green-100 bg-green-50 p-4 text-sm text-green-800">
              Outreach queued. We’ll keep the page updated as statuses change.
            </div>
          )}

          {error && <p className="text-sm text-red-600">{error}</p>}
          {feedback && <p className="text-sm text-gray-700">{feedback}</p>}

          {outreachStatuses.length > 0 && (
            <div className="space-y-2 rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] p-4">
              <p className="text-sm font-semibold text-gray-900">Delivery status</p>
              {outreachStatuses.slice(0, 5).map((item) => (
                <div key={item.candidateId} className="flex items-center justify-between text-sm">
                  <span className="text-gray-700">{item.candidateId.slice(0, 8)}</span>
                  <Badge variant="medium">{item.status}</Badge>
                </div>
              ))}
            </div>
          )}

          <Button
            className="w-full justify-center"
            onClick={() => router.push("/ready")}
            disabled={!isOutreachComplete}
          >
            Continue
          </Button>
        </CardContent>
      </Card>
    </AppShell>
  );
}

export default function OutreachPage() {
  return (
    <Suspense fallback={<div className="mx-auto w-full max-w-[560px] p-6 text-sm text-gray-600">Loading outreach page...</div>}>
      <OutreachContent />
    </Suspense>
  );
}
