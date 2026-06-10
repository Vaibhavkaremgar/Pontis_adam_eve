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
import { completeOrchestrationVoice, startOrchestrationVoice, type OrchestrationVoiceStartData } from "@/lib/api/orchestration";

type TranscriptRole = "assistant" | "user";
type SlackTranscriptTurn = {
  role: TranscriptRole;
  text: string;
};

function normalize(text: string) {
  return text.trim().replace(/\s+/g, " ");
}

function extractTranscriptEvent(message: unknown): SlackTranscriptTurn | null {
  if (!message || typeof message !== "object") return null;
  const record = message as Record<string, unknown>;
  if (record.type !== "transcript") return null;
  const text = normalize(String(record.transcript || ""));
  if (!text) return null;
  return {
    role: record.role === "assistant" ? "assistant" : "user",
    text,
  };
}

function buildTranscript(turns: SlackTranscriptTurn[]) {
  return turns.map((turn) => `${turn.role === "assistant" ? "Adam" : "Recruiter"}: ${turn.text}`).join("\n");
}

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
  const transcriptRef = useRef<SlackTranscriptTurn[]>([]);
  const completeRef = useRef(false);
  const previousStateRef = useRef<{
    jobId: string;
    job: typeof job;
    company: typeof company;
    isRefined: boolean;
    voiceNotes: string[];
    callStatus: typeof callStatus;
  } | null>(null);

  useEffect(() => {
    previousStateRef.current = {
      jobId,
      job,
      company,
      isRefined,
      voiceNotes,
      callStatus,
    };

    let cancelled = false;

    const bootstrap = async () => {
      try {
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
      completeRef.current = false;
      transcriptRef.current = [];
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
  }, [callStatus, company, job, jobId, setCallStatus, setCompany, setIsRefined, setJob, setJobId, setVoiceNotes, token, voiceNotes, isRefined]);

  useEffect(() => {
    if (!session) return;

    let cancelled = false;
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
      if (shouldBlockReviewNavigation(url)) return undefined;
      return originalPushState(state, title, url);
    }) as History["pushState"];

    history.replaceState = ((state: unknown, title: string, url?: string | URL | null) => {
      if (shouldBlockReviewNavigation(url)) return undefined;
      return originalReplaceState(state, title, url);
    }) as History["replaceState"];

    const attachBridge = () => {
      if (cancelled) return false;
      const vapi = (window as Window & { vapi?: { on: (event: string, handler: (payload: unknown) => void) => void } }).vapi;
      if (!vapi) return false;

      const handleMessage = (message: unknown) => {
        const event = extractTranscriptEvent(message);
        if (!event) return;
        transcriptRef.current.push(event);
      };

      const handleCallEnd = async () => {
        if (cancelled || completeRef.current) return;
        completeRef.current = true;
        const transcript = buildTranscript(transcriptRef.current);
        const voiceNotesPayload = transcriptRef.current.map((turn) => turn.text).filter(Boolean);
        try {
          const completion = await completeOrchestrationVoice(token, {
            transcript,
            voiceNotes: voiceNotesPayload,
          });
          if (!completion.success) {
            console.error("Slack voice completion failed", completion.error || "Unknown completion error");
          }
        } catch (err) {
          console.error("Slack voice completion failed", err);
        }
      };

      vapi.on("message", handleMessage);
      vapi.on("call-end", handleCallEnd);
      return true;
    };

    const interval = window.setInterval(() => {
      if (attachBridge()) {
        window.clearInterval(interval);
      }
    }, 100);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
      history.pushState = originalPushState;
      history.replaceState = originalReplaceState;
    };
  }, [session, token]);

  if (error) {
    return <div className="mx-auto w-full max-w-2xl px-4 py-6 text-sm text-red-700">{error}</div>;
  }

  if (!session) {
    return <div className="mx-auto w-full max-w-2xl px-4 py-6 text-sm text-gray-600">Loading voice intake...</div>;
  }

  return <VoiceUi />;
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

export default function VoicePage() {
  return (
    <Suspense fallback={<div className="mx-auto w-full max-w-2xl px-4 py-6 text-sm text-gray-600">Loading voice intake...</div>}>
      <VoicePageContent />
    </Suspense>
  );
}
