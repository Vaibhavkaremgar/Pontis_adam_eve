"use client";

/**
 * What this file does:
 * Submits voice refinements and refreshes candidate list.
 *
 * What API it connects to:
 * Uses /lib/api/voice -> POST /voice/refine and /lib/api/candidates -> GET /candidates?refined=true.
 *
 * How it fits in the pipeline:
 * Bridges recruiter voice input into backend candidate re-ranking flow.
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAppContext } from "@/context/AppContext";
import { getCandidates } from "@/lib/api/candidates";
import { refineWithVoice } from "@/lib/api/voice";
import { cn } from "@/lib/utils";

const checks = [
  "Analyzing voice notes",
  "Applying must-haves and red-flag constraints",
  "Refreshing ranked candidates",
  "Preparing refined shortlist"
];

export default function VoiceProcessingPage() {
  const router = useRouter();
  const { user, isSessionReady, jobId, voiceNotes, setCandidates, setIsRefined } = useAppContext();
  const [completed, setCompleted] = useState(0);
  const [error, setError] = useState("");
  const emptyTranscriptWarning =
    jobId && voiceNotes.length === 0
      ? "No transcript found. Please complete voice intake before refining candidates."
      : "";

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

  useEffect(() => {
    let isMounted = true;

    const run = async () => {
      // This handles real-world API delays and failures.
      setCompleted(1);

      const refineResult = await refineWithVoice({ jobId, voiceNotes });
      if (!refineResult.success) {
        if (isMounted) {
          setError(refineResult.error || "Could not refine candidates right now.");
        }
        return;
      }

      setCompleted(2);

      const refreshedResult = await getCandidates({ jobId, refined: true });
      if (!refreshedResult.success || !refreshedResult.data) {
        if (isMounted) {
          setError(refreshedResult.error || "Could not load refined candidates.");
        }
        return;
      }

      if (isMounted) {
        setCompleted(4);
        setCandidates(refreshedResult.data);
        setIsRefined(true);
      }
    };

    if (jobId && voiceNotes.length > 0) {
      run();
    }

    return () => {
      isMounted = false;
    };
  }, [jobId, setCandidates, setIsRefined, voiceNotes]);

  return (
    <AppShell activeStep={3}>
      <div className="mx-auto w-full max-w-[560px]">
        <Card>
          <CardHeader className="text-center">
            <CardTitle>Refining candidates...</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {checks.map((item, idx) => (
              <div
                key={item}
                className={cn(
                  "flex items-center justify-between rounded-xl border p-3 text-sm",
                  idx < completed ? "border-green-200 bg-green-50" : "border-[#E5E7EB] bg-white"
                )}
              >
                <p className="text-gray-600">{item}</p>
                <Badge variant={idx < completed ? "high" : "neutral"}>
                  {idx < completed ? "Done" : "Pending"}
                </Badge>
              </div>
            ))}

            {(error || emptyTranscriptWarning) && (
              <p className="text-sm text-red-600">{error || emptyTranscriptWarning}</p>
            )}

            <Link
              href="/outreach"
              className={cn(
                buttonVariants({ variant: "default" }),
                "mt-2 w-full justify-center",
                (completed < checks.length || Boolean(error) || Boolean(emptyTranscriptWarning)) &&
                  "pointer-events-none opacity-50"
              )}
            >
              Continue to Outreach
            </Link>
          </CardContent>
        </Card>
      </div>
    </AppShell>
  );
}
