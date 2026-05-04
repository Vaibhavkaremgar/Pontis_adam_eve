"use client";

/**
 * What this file does:
 * Fallback processing page — only reached if recruiter manually navigates here
 * or if the auto-pipeline in voice-ui.tsx failed and they need to retry.
 *
 * Normal flow: voice-ui.tsx auto-triggers refine + candidates and navigates
 * directly to /review. This page is a safety net.
 *
 * What API it connects to:
 * POST /voice/refine  and  GET /candidates?refresh=true
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAppContext } from "@/context/AppContext";
import { getCandidatesWithMode } from "@/lib/api/candidates";
import { refineWithVoice } from "@/lib/api/voice";
import { cn } from "@/lib/utils";

const STEPS = [
  "Analysing conversation",
  "Updating job profile",
  "Re-embedding job vector",
  "Fetching ranked candidates",
];

export default function VoiceProcessingPage() {
  const router = useRouter();
  const { user, isSessionReady, jobId, voiceNotes, setCandidates, setIsRefined } = useAppContext();
  const [completed, setCompleted] = useState(0);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) { router.replace("/login"); return; }
    if (!jobId) { router.replace("/job"); }
  }, [isSessionReady, jobId, router, user]);

  useEffect(() => {
    if (!jobId || voiceNotes.length === 0) return;

    let cancelled = false;

    const run = async () => {
      const fullTranscript = voiceNotes.join("\n");

      setCompleted(1);
      const refineResult = await refineWithVoice({
        jobId,
        voiceNotes,
        transcript: fullTranscript,
      });

      if (cancelled) return;

      if (!refineResult.success) {
        setError(refineResult.error || "Could not refine job. Proceeding with original.");
        // Soft failure — still try to fetch candidates
      }

      setCompleted(3);

      const candidatesResult = await getCandidatesWithMode({ jobId, mode: "volume", refresh: true });
      if (cancelled) return;

      if (!candidatesResult.success || !candidatesResult.data) {
        setError(candidatesResult.error || "Could not load candidates.");
        return;
      }

      setCandidates(candidatesResult.data);
      setIsRefined(true);
      setCompleted(4);
      setDone(true);
    };

    run();
    return () => { cancelled = true; };
  }, [jobId, voiceNotes, setCandidates, setIsRefined]);

  const noTranscript = jobId && voiceNotes.length === 0;

  return (
    <AppShell activeStep={3}>
      <div className="mx-auto w-full max-w-[560px]">
        <Card>
          <CardHeader className="text-center">
            <CardTitle>{done ? "Candidates ready" : "Processing voice intake..."}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {noTranscript ? (
              <div className="space-y-4">
                <p className="text-sm text-red-600">
                  No transcript found. Please complete voice intake before continuing.
                </p>
                <Button className="w-full justify-center" onClick={() => router.push("/voice")}>
                  Back to Voice Intake
                </Button>
              </div>
            ) : (
              <>
                {STEPS.map((label, idx) => (
                  <div
                    key={label}
                    className={cn(
                      "flex items-center justify-between rounded-xl border p-3 text-sm",
                      idx < completed ? "border-green-200 bg-green-50" : "border-[rgba(120,100,80,0.08)] bg-[#F3EDE3]"
                    )}
                  >
                    <p className="text-gray-600">{label}</p>
                    <Badge variant={idx < completed ? "high" : "neutral"}>
                      {idx < completed ? "Done" : "Pending"}
                    </Badge>
                  </div>
                ))}

                {error && <p className="text-sm text-amber-600">⚠ {error}</p>}

                <Button
                  className="mt-2 w-full justify-center"
                  disabled={!done}
                  onClick={() => router.push("/outreach")}
                >
                  {done ? "Continue to Outreach" : "Processing..."}
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </AppShell>
  );
}
