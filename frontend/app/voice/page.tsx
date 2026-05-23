"use client";

/**
 * What this file does:
 * Renders the voice intake page inside the shared intake shell.
 *
 * What API it connects to:
 * No direct API calls in this page; VoiceUi handles the Vapi session and candidate refresh.
 *
 * How it fits in the pipeline:
 * Uses the same navbar and stepper as the other intake steps for a consistent experience.
 */
import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { VoiceUi } from "@/components/voice/voice-ui";
import { Button } from "@/components/ui/button";
import { useAppContext } from "@/context/AppContext";

export default function VoicePage() {
  const router = useRouter();
  const { user, isSessionReady, jobId, job, company, isRefined } = useAppContext();
  const isPendingJobCreation = !jobId && Boolean(job.title.trim() || company.name.trim());

  useEffect(() => {
    if (!isSessionReady) return;

    if (!user) {
      router.replace("/login");
      return;
    }

    if (!jobId && !isPendingJobCreation) {
      router.replace("/job");
      return;
    }
  }, [isPendingJobCreation, isSessionReady, jobId, router, user]);

  if (isPendingJobCreation && !jobId) {
    return (
      <AppShell activeStep={3}>
        <div className="mx-auto flex min-h-[40vh] w-full max-w-2xl items-center justify-center px-4">
          <div className="w-full rounded-3xl border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] p-6 text-center shadow-[0_4px_12px_rgba(0,0,0,0.02)]">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-[#166534]">Preparing intake</p>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight text-gray-900">Your voice intake is loading</h1>
            <p className="mt-3 text-sm text-gray-600">
              We moved you forward right away. The job record is still syncing in the background.
            </p>
            <div className="mt-5 flex justify-center">
              <Button variant="outline" onClick={() => router.push("/job")}>
                Back to Job Details
              </Button>
            </div>
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell activeStep={3} contentClassName="mx-auto w-full max-w-2xl px-4 py-5 md:px-5 lg:px-6">
      <VoiceUi />

      <div className="mt-6 flex justify-end">
        <Button
          onClick={() => router.push("/review")}
          disabled={!isRefined || !jobId}
        >
          Continue to Review
        </Button>
      </div>
    </AppShell>
  );
}
