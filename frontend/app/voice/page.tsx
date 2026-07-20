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
import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { VoiceUi } from "@/components/voice/voice-ui";
import { Button } from "@/components/ui/button";
import { initialCompany, initialJob, useAppContext } from "@/context/AppContext";
import { startOrchestrationVoice, type OrchestrationVoiceStartData } from "@/lib/api/orchestration";

function buildVoiceJob(session: OrchestrationVoiceStartData) {
  const variables = session.variableValues || {};
  return {
    ...initialJob,
    title: String(variables.roleTitle || ""),
    description: String(variables.conversationSummary || ""),
    location: String(variables.location || ""),
    compensation: String(variables.compensation || ""),
    workAuthorization: initialJob.workAuthorization,
    remotePolicy: initialJob.remotePolicy,
    experienceRequired: String(variables.experienceRequired || ""),
    vettingMode: initialJob.vettingMode,
    autoExportToAts: false,
  };
}

function buildVoiceCompany(session: OrchestrationVoiceStartData) {
  const variables = session.variableValues || {};
  return {
    ...initialCompany,
    name: String(variables.companyName || ""),
  };
}

function SlackVoiceBridge({ token }: { token: string }) {
  const { jobId, job, company, isRefined, voiceNotes, callStatus, setJobId, setJob, setCompany, setIsRefined, setVoiceNotes, setCallStatus } = useAppContext();
  const [session, setSession] = useState<OrchestrationVoiceStartData | null>(null);
  const [error, setError] = useState("");
  const previousStateRef = useRef<{
    jobId: string;
    job: typeof job;
    company: typeof company;
    isRefined: boolean;
    voiceNotes: string[];
    callStatus: typeof callStatus;
  } | null>(null);

  useEffect(() => {
    console.log("SlackVoiceBridge bootstrap");
    previousStateRef.current = {
      jobId,
      job,
      company,
      isRefined,
      voiceNotes,
      callStatus,
    };
  }, []);

  useEffect(() => {
    return () => {
      console.log("SlackVoiceBridge cleanup");
      const previousState = previousStateRef.current;
      if (previousState) {
        setJobId(previousState.jobId);
        setJob(previousState.job);
        setCompany(previousState.company);
        setIsRefined(previousState.isRefined);
        setVoiceNotes(previousState.voiceNotes);
        setCallStatus(previousState.callStatus);
      }
    };
  }, [setCallStatus, setCompany, setIsRefined, setJob, setJobId, setVoiceNotes]);

  useEffect(() => {
    let cancelled = false;

    const bootstrap = async () => {
      try {
        console.log("startOrchestrationVoice called");
        const result = await startOrchestrationVoice(token);
        if (cancelled) return;
        if (!result.success || !result.data) {
          setError(result.error || "Could not load the Slack voice session.");
          return;
        }

        setSession(result.data);
        setJobId(String(result.data.session?.jobId || ""));
        setJob(buildVoiceJob(result.data));
        setCompany(buildVoiceCompany(result.data));
        setIsRefined(false);
        setVoiceNotes([]);
        setCallStatus("idle");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load the voice session.");
      }
    };

    void bootstrap();

    return () => {
      cancelled = true;
    };
  }, [setCallStatus, setCompany, setIsRefined, setJob, setJobId, setVoiceNotes, token]);

  useEffect(() => {
    if (!session) return;
    const history = window.history as History & {
      pushState: History["pushState"];
      replaceState: History["replaceState"];
    };
    const originalPushState = history.pushState.bind(window.history);
    const originalReplaceState = history.replaceState.bind(window.history);

    const shouldBlockReviewNavigation = (url: string | URL | null | undefined) => {
      if (!url) return false;
      const resolvedUrl = typeof url === "string" ? new URL(url, window.location.origin) : url;
      return resolvedUrl.pathname === "/review";
    };

    history.pushState = ((state: unknown, title: string, url?: string | URL | null) => {
      if (shouldBlockReviewNavigation(url)) {
        console.info("dashboard_navigation_suppressed", {
          source: "SlackVoiceBridge",
          method: "pushState",
          url: typeof url === "string" ? url : url?.toString() || "",
        });
        return undefined;
      }
      return originalPushState(state, title, url);
    }) as History["pushState"];

    history.replaceState = ((state: unknown, title: string, url?: string | URL | null) => {
      if (shouldBlockReviewNavigation(url)) {
        console.info("dashboard_navigation_suppressed", {
          source: "SlackVoiceBridge",
          method: "replaceState",
          url: typeof url === "string" ? url : url?.toString() || "",
        });
        return undefined;
      }
      return originalReplaceState(state, title, url);
    }) as History["replaceState"];

    return () => {
      history.pushState = originalPushState;
      history.replaceState = originalReplaceState;
    };
  }, [session]);

  if (error) {
    return <div className="mx-auto w-full max-w-2xl px-4 py-6 text-sm text-red-700">{error}</div>;
  }

  if (!session) {
    return <div className="mx-auto w-full max-w-2xl px-4 py-6 text-sm text-gray-600">Loading voice intake...</div>;
  }

  return <VoiceUi completionMode="slack" slackToken={token} />;
}

function VoicePageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const slackToken = (searchParams.get("token") || "").trim();
  const { user, isSessionReady, jobId, job, company, isRefined } = useAppContext();
  const isPendingJobCreation = !jobId && Boolean(job.title.trim() || company.name.trim());

  useEffect(() => {
    if (slackToken) return;
    if (!isSessionReady) return;

    if (!user) {
      router.replace("/login");
      return;
    }

    if (!jobId && !isPendingJobCreation) {
      router.replace("/job");
      return;
    }
  }, [isPendingJobCreation, isSessionReady, jobId, router, slackToken, user]);

  if (slackToken) {
    return <SlackVoiceBridge token={slackToken} />;
  }

  if (isPendingJobCreation && !jobId) {
    return (
      <AppShell activeStep={3}>
        <div className="mx-auto flex min-h-[40vh] w-full max-w-2xl items-center justify-center px-4">
          <div className="w-full rounded-3xl border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] p-6 text-center shadow-[0_4px_12px_rgba(0,0,0,0.02)]">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-[#166534]">Preparing intake</p>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight text-gray-900">Your voice intake is loading</h1>
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

export default function VoicePage() {
  return (
    <Suspense fallback={<div className="mx-auto w-full max-w-2xl px-4 py-6 text-sm text-gray-600">Loading voice intake...</div>}>
      <VoicePageContent />
    </Suspense>
  );
}
