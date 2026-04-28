"use client";

/**
 * What this file does:
 * Renders the voice intake page inside the shared intake shell.
 *
 * What API it connects to:
 * No direct API calls in this page; conversation output is sent in /voice/processing via API layer.
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
  const { user, isSessionReady, jobId, isRefined } = useAppContext();

  useEffect(() => {
    if (!isSessionReady) return;

    if (!user) {
      router.replace("/login");
      return;
    }

    if (!jobId) {
      router.replace("/job");
      return;
    }
  }, [isSessionReady, jobId, router, user]);

  return (
    <AppShell activeStep={4}>
      <VoiceUi />

      <div className="mt-8 flex justify-end">
        <Button
          onClick={() => router.push(`/outreach?jobId=${encodeURIComponent(jobId)}`)}
          disabled={!isRefined || !jobId}
        >
          Continue to Outreach
        </Button>
      </div>
    </AppShell>
  );
}
