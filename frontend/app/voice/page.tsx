"use client";

/**
 * What this file does:
 * Renders the full-width Voice Intake page with Perfectly-style structure and spacing.
 *
 * What API it connects to:
 * No direct API calls in this page; conversation output is sent in /voice/processing via API layer.
 *
 * How it fits in the pipeline:
 * Layout is centered to keep focus on conversational intake and maintain visual consistency.
 * Minimal UI is required to reduce distractions and mirror Perfectly-style guided interaction.
 * The structure/spacing intentionally matches Perfectly UX proportions for familiarity.
 */
import { Check } from "lucide-react";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { VoiceUi } from "@/components/voice/voice-ui";
import { useAppContext } from "@/context/AppContext";
import { cn } from "@/lib/utils";

const STEPS = [
  { id: 1, label: "Company", state: "done" as const },
  { id: 2, label: "Job", state: "done" as const },
  { id: 3, label: "Voice Intake", state: "active" as const },
  { id: 4, label: "Outreach", state: "inactive" as const },
  { id: 5, label: "Ready", state: "inactive" as const }
];

export default function VoicePage() {
  const router = useRouter();
  const { user, isSessionReady, jobId, logout } = useAppContext();

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

  return (
    <main className="min-h-screen w-full bg-[#FAF7F2]">
      <div className="mx-auto w-full max-w-4xl px-4 py-8 md:px-6 md:py-10">
        <div className="mb-8 flex items-center justify-between">
          <button
            onClick={() => router.push("/job")}
            className="font-body text-sm font-medium text-[#111111] hover:opacity-70"
          >
            Back to Jobs
          </button>

          <p className="font-heading text-2xl leading-none text-[#111111]">Pontis</p>

          <button onClick={logout} className="font-body text-sm font-medium text-[#111111] hover:opacity-70">
            Sign out
          </button>
        </div>

        <div className="mb-10">
          <div className="grid grid-cols-5 items-start gap-2">
            {STEPS.map((step, index) => (
              <div key={step.id} className="relative flex flex-col items-center gap-2">
                {index < STEPS.length - 1 && <div className="absolute left-1/2 top-4 h-px w-full bg-gray-200" />}

                <div
                  className={cn(
                    "relative z-10 flex h-8 w-8 items-center justify-center rounded-full text-sm font-semibold",
                    step.state === "active" && "bg-orange-500 text-white",
                    step.state === "done" && "bg-orange-500 text-white",
                    step.state === "inactive" && "bg-gray-200 text-gray-500"
                  )}
                >
                  {step.state === "done" ? <Check className="h-4 w-4" /> : step.id}
                </div>

                <p className="font-body text-xs text-[#6B7280]">{step.label}</p>
              </div>
            ))}
          </div>
        </div>

        <VoiceUi />
      </div>
    </main>
  );
}
