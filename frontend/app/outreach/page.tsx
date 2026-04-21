"use client";

/**
 * What this file does:
 * Lets recruiter select candidates and submit outreach request.
 *
 * What API it connects to:
 * Uses /lib/api/outreach -> POST /outreach.
 *
 * How it fits in the pipeline:
 * Sends selected candidates for backend outreach orchestration while frontend handles state and UX.
 */
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { useAppContext } from "@/context/AppContext";
import { sendOutreach } from "@/lib/api/outreach";

const emailTemplate = `Subject: Quick Intro - {Job Title} Opportunity

Hi {Candidate Name},

I came across your background and thought you could be a strong match for our {Job Title} role.
Would you be open to a short conversation this week?

Best,
{Recruiter Name}`;

export default function OutreachPage() {
  const router = useRouter();
  const { user, isSessionReady, jobId, candidates, isRefined } = useAppContext();
  const [selectedCandidates, setSelectedCandidates] = useState<string[]>([]);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isOutreachComplete, setIsOutreachComplete] = useState(false);
  const shortlisted = useMemo(
    () => candidates.filter((candidate) => ["shortlisted", "contacted", "exported", "interview_scheduled"].includes(candidate.status)),
    [candidates]
  );

  useEffect(() => {
    if (!isSessionReady) return;

    if (!user) {
      router.replace("/login");
      return;
    }

    if (!jobId) {
      router.replace("/job");
    }
  }, [isSessionReady, jobId, router, user]);

  const canSubmit = useMemo(() => selectedCandidates.length > 0 && !isSubmitting, [isSubmitting, selectedCandidates]);

  const toggleCandidate = (candidateId: string) => {
    setSelectedCandidates((prev) =>
      prev.includes(candidateId) ? prev.filter((id) => id !== candidateId) : [...prev, candidateId]
    );
  };

  const handleSendOutreach = async () => {
    // This handles real-world API delays and failures.
    if (!canSubmit) return;

    setIsSubmitting(true);
    setError("");
    setFeedback("");
    setIsOutreachComplete(false);

    const result = await sendOutreach({ jobId, selectedCandidates });
    if (!result.success || !result.data) {
      setError(result.error || "Failed to send outreach.");
      setIsSubmitting(false);
      return;
    }

    setFeedback(result.data.message);
    setIsOutreachComplete(true);
    setIsSubmitting(false);
  };

  return (
    <AppShell activeStep={4}>
      <Card className="mx-auto w-full max-w-[560px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Candidate Outreach</CardTitle>
          <CardDescription>Select candidates and send to outreach workflow</CardDescription>
        </CardHeader>

        <CardContent className="space-y-6">
          {isRefined && (
            <div className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
              Refined based on your input
            </div>
          )}

          <div className="space-y-3">
            {shortlisted.map((candidate) => (
              <label
                key={candidate.id}
                className="flex cursor-pointer items-start justify-between rounded-xl border border-[#E5E7EB] bg-white p-4"
              >
                <div className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    className="mt-1 h-4 w-4"
                    checked={selectedCandidates.includes(candidate.id)}
                    onChange={() => toggleCandidate(candidate.id)}
                    disabled={isSubmitting}
                  />
                  <div className="space-y-1">
                    <p className="font-semibold text-gray-900">{candidate.name || candidate.id.slice(0, 8)}</p>
                    <p className="text-sm text-gray-600">{candidate.role}</p>
                  </div>
                </div>
                <Badge
                  variant={
                    candidate.status === "exported" || candidate.status === "interview_scheduled"
                      ? "high"
                      : candidate.status === "contacted"
                        ? "medium"
                        : "low"
                  }
                >
                  {candidate.outreachStatus ? `${candidate.status} • ${candidate.outreachStatus}` : candidate.status}
                </Badge>
              </label>
            ))}
            {shortlisted.length === 0 && (
              <div className="rounded-xl border border-[#E5E7EB] bg-gray-50 p-4 text-sm text-gray-600">
                No shortlisted candidates yet. Review candidates first.
              </div>
            )}
          </div>

          <Button className="w-full justify-center" onClick={handleSendOutreach} disabled={!canSubmit || shortlisted.length === 0}>
            {isSubmitting ? "Loading..." : "Send for Outreach"}
          </Button>

          {error && <p className="text-sm text-red-600">{error}</p>}
          {feedback && <p className="text-sm text-gray-700">{feedback}</p>}

          <Separator />

          <div className="space-y-3">
            <p className="text-sm font-semibold text-gray-900">Email Preview</p>
            <pre className="max-h-[260px] overflow-y-auto whitespace-pre-wrap rounded-xl border border-[#E5E7EB] bg-gray-50 p-4 text-xs text-gray-600">
              {emailTemplate}
            </pre>
          </div>

          <Button className="w-full justify-center" onClick={() => router.push("/ready")} disabled={!isOutreachComplete}>
            Continue
          </Button>
        </CardContent>
      </Card>
    </AppShell>
  );
}
