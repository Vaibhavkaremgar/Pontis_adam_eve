"use client";

/**
 * What this file does:
 * Shows the legacy outreach fallback and delivery preview.
 * The real outreach send now happens automatically from the Review flow.
 *
 * What API it connects to:
 * GET /candidates/shortlisted  — server-side shortlisted-only list
 * GET /outreach/preview        — fetches the legacy outreach draft
 */
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { useAppContext } from "@/context/AppContext";
import { getShortlistedCandidates } from "@/lib/api/candidates";
import { getEmailPreview } from "@/lib/api/outreach";
import { getStoredShortlistedCandidateIds, getStoredShortlistedCandidates } from "@/lib/session";
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
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);

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
    void (async () => {
      try {
        const result = await getShortlistedCandidates(jobId);
        const preferredIds = getStoredShortlistedCandidateIds(jobId);
        const storedCandidates = getStoredShortlistedCandidates(jobId);
        const backendCandidates = result.success && result.data ? result.data : [];
        const storedCandidatesById = new Map(storedCandidates.map((candidate) => [candidate.id, candidate] as const));

        const shortlistedCandidates = preferredIds.length > 0
          ? preferredIds
              .map((candidateId) =>
                backendCandidates.find((candidate) => candidate.id === candidateId) ||
                storedCandidatesById.get(candidateId)
              )
              .filter((candidate): candidate is Candidate => Boolean(candidate))
          : backendCandidates.length > 0
            ? backendCandidates
            : storedCandidates;

        setShortlisted(shortlistedCandidates);
        setSelectedCandidates((prev) => {
          if (prev.length > 0) return prev.slice(0, 1);
          return shortlistedCandidates.length > 0 ? [shortlistedCandidates[0].id] : [];
        });
      } finally {
        setIsLoadingCandidates(false);
      }
    })();
  }, [isSessionReady, user, jobId]);

  // When selection changes to exactly one candidate, fetch the real preview from backend.
  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();

    if (selectedCandidates.length === 0) {
      setEmailSubject("");
      setEmailBody("");
      setPreviewToEmail("");
      return;
    }

    if (selectedCandidates.length === 1) {
      setIsLoadingPreview(true);
      getEmailPreview(jobId, selectedCandidates[0]).then((result) => {
        if (controller.signal.aborted) return;
        if (result.success && result.data) {
          setEmailSubject(result.data.subject);
          setEmailBody(result.data.body);
          setPreviewToEmail(result.data.toEmail);
        }
        setIsLoadingPreview(false);
      });
      return () => controller.abort();
    }

    setEmailSubject("Personalised email per candidate");
    setEmailBody(
      "Each candidate will receive a personalised email generated from their profile and the job details.\n\n" +
      "Select a single candidate to preview and edit their specific email before sending."
    );
    setPreviewToEmail("");
    return () => controller.abort();
  }, [selectedCandidates, jobId]);

  const toggleCandidate = (candidateId: string) => {
    setSelectedCandidates((prev) => (prev[0] === candidateId ? [] : [candidateId]));
  };

  const handleSendOutreach = async () => {
    router.push("/review");
  };

  return (
    <AppShell activeStep={5}>
      <Card className="mx-auto w-full max-w-[560px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Outreach status</CardTitle>
          <CardDescription>Outreach now starts automatically from Review. This page is a legacy fallback for delivery details.</CardDescription>
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
                    disabled={false}
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

          <div className="rounded-xl border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-900">
            Review now auto-sends outreach when you select a candidate. Return to Review to continue.
          </div>

          {/* Email preview */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold text-gray-900">Selected Candidate Preview</p>
              {selectedCandidates.length === 1 && previewToEmail && (
                <p className="text-xs text-gray-500">
                  To: {previewToEmail}
                </p>
              )}
            </div>

            {selectedCandidates.length === 0 && (
              <div className="rounded-xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4 text-sm text-gray-500">
                Select exactly one candidate above to preview the legacy outreach draft.
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
                  onChange={() => undefined}
                  disabled
                  placeholder="Outreach now starts automatically from Review."
                />
            {selectedCandidates.length === 1 && (
              <p className="text-xs text-gray-400">
                Outreach is selection-driven now. This route only shows the legacy draft.
              </p>
            )}
          </>
            )}
          </div>

          <Button
            className="w-full justify-center"
            onClick={handleSendOutreach}
            disabled={false}
          >
            Back to Review
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
