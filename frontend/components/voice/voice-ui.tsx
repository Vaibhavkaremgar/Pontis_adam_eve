"use client";

/**
 * What this component does:
 * Full production voice intake UI.
 * - Starts Vapi directly with job context injected as variableValues + dynamic firstMessage
 * - Captures BOTH assistant and user turns as structured VoiceTurn[]
 * - On call-end: auto-triggers POST /voice/refine with full conversation transcript
 * - Then auto-triggers GET /candidates?refresh=true
 * - Navigates to /review on success, shows retry on failure
 */
import { AnimatePresence, motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import Vapi from "@vapi-ai/web";

import { useAppContext } from "@/context/AppContext";
import { getCandidatesWithMode } from "@/lib/api/candidates";
import { refineWithVoice } from "@/lib/api/voice";

import { ChatBubble, type ChatMessage } from "./chat-bubble";
import { WaveAnimation } from "./wave-animation";

// ─── types ────────────────────────────────────────────────────────────────────

type VoiceTurn = {
  role: "assistant" | "user";
  text: string;
};

// ─── helpers ──────────────────────────────────────────────────────────────────

function normalize(value: string) {
  return value.trim().replace(/\s+/g, " ");
}

function toErrorMessage(error: unknown): string {
  if (!error) return "Unknown error";
  if (typeof error === "string") return error;
  if (typeof error === "object") {
    const record = error as Record<string, unknown>;
    const message = record.message;
    if (typeof message === "string" && message.trim()) return message;
    try {
      return JSON.stringify(record);
    } catch {
      return "Unknown error object";
    }
  }
  return String(error);
}

function buildFullTranscript(turns: VoiceTurn[]): string {
  return turns
    .map((t) => `${t.role === "assistant" ? "Adam" : "Recruiter"}: ${t.text}`)
    .join("\n");
}

function extractTranscriptEvent(message: unknown): { role: "assistant" | "user"; text: string; isFinal: boolean } | null {
  if (!message || typeof message !== "object") return null;
  const r = message as Record<string, unknown>;
  if (r.type !== "transcript") return null;
  if (typeof r.transcript !== "string") return null;
  const text = normalize(r.transcript);
  if (!text) return null;
  const role: "assistant" | "user" = r.role === "assistant" ? "assistant" : "user";
  return { role, text, isFinal: r.transcriptType === "final" };
}

function classifyVoiceError(error: unknown): { kind: string; message: string } {
  if (!error || typeof error !== "object") {
    return { kind: "unknown", message: "Unknown voice error" };
  }

  const record = error as Record<string, unknown>;
  const nested = (record.error && typeof record.error === "object" ? record.error : null) as Record<string, unknown> | null;
  const type = String(nested?.type || record.type || "").trim();
  const message = String(nested?.msg || record.errorMsg || record.message || "Unknown voice error").trim();

  if (type === "ejected") {
    return { kind: "ejected", message: "The voice assistant was disconnected." };
  }

  return { kind: type || "unknown", message: message || "Voice assistant failed to connect." };
}

// ─── component ────────────────────────────────────────────────────────────────

export function VoiceUi() {
  const router = useRouter();
  const { callStatus, setCallStatus, setVoiceNotes, setCandidates, setIsRefined, jobId, job, company, user } = useAppContext();

  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [pipelineStatus, setPipelineStatus] = useState<"idle" | "refining" | "fetching" | "done" | "error">("idle");
  const [pipelineError, setPipelineError] = useState("");

  // Refs — never cause re-renders, safe to read inside Vapi callbacks
  const vapiRef = useRef<Vapi | null>(null);
  const turnsRef = useRef<VoiceTurn[]>([]);       // full structured conversation
  const firedRef = useRef(false);                  // guard against double pipeline trigger
  const callStartedAtRef = useRef<number | null>(null);
  const terminalStateRef = useRef<"idle" | "starting" | "live" | "manual-stop" | "ejected" | "error" | "done">("idle");

  // ── scroll chat to bottom on new messages ──────────────────────────────────
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    chatScrollRef.current?.scrollTo({ top: chatScrollRef.current.scrollHeight, behavior: "smooth" });
  }, [chatMessages]);

  // ── pipeline: refine → fetch candidates → navigate ─────────────────────────
  const runPipeline = useCallback(async (turns: VoiceTurn[]) => {
    if (firedRef.current) return;
    firedRef.current = true;

    const fullTranscript = buildFullTranscript(turns);
    const endedAt = Date.now();

    if (!fullTranscript.trim()) {
      setPipelineStatus("error");
      setPipelineError("No conversation was captured. Please try again.");
      return;
    }

    console.info("[voice] pipeline_start", {
      jobId,
      durationMs: callStartedAtRef.current ? endedAt - callStartedAtRef.current : null,
      turnsCaptured: turns.length,
    });

    // Store voiceNotes for any downstream consumers (outreach, etc.)
    setVoiceNotes([fullTranscript]);

    setPipelineStatus("refining");
    const refineResult = await refineWithVoice({
      jobId,
      voiceNotes: [fullTranscript],
      transcript: fullTranscript,
    });

    if (!refineResult.success) {
      setPipelineStatus("error");
      setPipelineError(refineResult.error || "Could not refine job. Proceeding with original.");
      // Soft failure — still fetch candidates with original job
    }

    setPipelineStatus("fetching");
    const candidatesResult = await getCandidatesWithMode({
      jobId,
      mode: job.vettingMode || "volume",
      refresh: true,
    });

    if (!candidatesResult.success || !candidatesResult.data) {
      setPipelineStatus("error");
      setPipelineError(candidatesResult.error || "Could not load candidates.");
      return;
    }

    setCandidates(candidatesResult.data);
    setIsRefined(true);
    setPipelineStatus("done");
    terminalStateRef.current = "done";

    // Auto-navigate to review after a short pause so recruiter sees "done"
    setTimeout(() => router.push("/review"), 1200);
  }, [jobId, router, setCandidates, setIsRefined, setVoiceNotes]);

  // ── Vapi instance (created once per session) ───────────────────────────────
  const ensureVapi = useCallback(() => {
    if (vapiRef.current) return vapiRef.current;

    const publicKey = process.env.NEXT_PUBLIC_VAPI_PUBLIC_KEY;
    if (!publicKey) throw new Error("NEXT_PUBLIC_VAPI_PUBLIC_KEY is not set.");

    const vapi = new Vapi(publicKey);
    console.log("[vapi] instance created");

    if (typeof window !== "undefined") {
      (window as Window & { vapi?: Vapi }).vapi = vapi;
      console.log("[vapi] instance attached to window.vapi");
    }

    vapi.on("call-start", () => {
      terminalStateRef.current = "live";
      if (!callStartedAtRef.current) {
        callStartedAtRef.current = Date.now();
      }
      console.info("[vapi] call-start", {
        jobId,
      });
      setCallStatus("listening");
    });

    vapi.on("speech-start", () => setCallStatus("speaking"));
    vapi.on("speech-end", () => setCallStatus("listening"));

    vapi.on("message", (message) => {
      const event = extractTranscriptEvent(message);
      if (!event) return;

      if (event.isFinal) {
        // Append final turn to structured log
        turnsRef.current = [...turnsRef.current, { role: event.role, text: event.text }];

        // Update chat UI — replace last partial bubble of same role or append
        setChatMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.role === event.role && !last.isFinal) {
            return [...prev.slice(0, -1), { role: event.role, text: event.text, isFinal: true }];
          }
          return [...prev, { role: event.role, text: event.text, isFinal: true }];
        });
      } else {
        // Live partial — update last bubble of same role in-place
        setChatMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.role === event.role && !last.isFinal) {
            return [...prev.slice(0, -1), { role: event.role, text: event.text, isFinal: false }];
          }
          return [...prev, { role: event.role, text: event.text, isFinal: false }];
        });
      }
    });

    vapi.on("error", (error) => {
      const classified = classifyVoiceError(error);
      terminalStateRef.current = classified.kind === "ejected" ? "ejected" : "error";
      const endedAt = Date.now();
      console.error("[vapi] error", {
        jobId,
        durationMs: callStartedAtRef.current ? endedAt - callStartedAtRef.current : null,
        kind: classified.kind,
        message: classified.message,
        error,
      });
      setCallStatus("error");
      setPipelineStatus("error");
      setPipelineError(classified.message);
    });
    vapi.on("call-start-failed", (event) => {
      terminalStateRef.current = "error";
      console.error("[vapi] call-start-failed", {
        jobId,
        event,
      });
      setCallStatus("error");
      setPipelineStatus("error");
      setPipelineError(`Unable to start voice session: ${event?.error || "unknown startup failure"}`);
    });

    vapi.on("call-end", () => {
      const endedAt = Date.now();
      console.info("[vapi] call-end", {
        jobId,
        durationMs: callStartedAtRef.current ? endedAt - callStartedAtRef.current : null,
        terminalState: terminalStateRef.current,
      });

      if (terminalStateRef.current === "ejected" || terminalStateRef.current === "error") {
        setCallStatus("error");
        return;
      }

      setCallStatus("completed");
      terminalStateRef.current = "done";
      // Auto-trigger pipeline with everything captured so far
      void runPipeline(turnsRef.current);
    });

    vapiRef.current = vapi;
    return vapi;
  }, [runPipeline, setCallStatus]);

  // ── start call ─────────────────────────────────────────────────────────────
  const handleStart = async () => {
    console.log("Start conversation clicked");
    const assistantId = process.env.NEXT_PUBLIC_VAPI_ASSISTANT_ID;
    if (!assistantId) {
      setCallStatus("error");
      setPipelineError("Voice assistant not configured. Add NEXT_PUBLIC_VAPI_ASSISTANT_ID.");
      return;
    }

    // Reset state
    setChatMessages([]);
    turnsRef.current = [];
    firedRef.current = false;
    callStartedAtRef.current = null;
    terminalStateRef.current = "starting";
    setPipelineStatus("idle");
    setPipelineError("");

    const jobTitle = job.title || "this role";
    const companyName = company.name || "your company";
    const jobDescription = job.description || "";
    const location = job.location || "";
    const recruiterName = user?.name || user?.email || "Recruiter";
    const jobContext = {
      title: job.title || "",
      description: job.description || "",
      location: job.location || "",
      compensation: job.compensation || "",
      workAuthorization: job.workAuthorization || "",
      remotePolicy: job.remotePolicy || "",
      experienceRequired: job.experienceRequired || "",
      autoExportToAts: Boolean(job.autoExportToAts),
    };
    const companyContext = {
      name: company.name || "",
      website: company.website || "",
      description: company.description || "",
      industry: company.industry || "",
      atsProvider: company.atsProvider || "",
      atsConnected: Boolean(company.atsConnected),
    };

    const firstMessage = companyName && jobTitle
      ? `You're hiring a ${jobTitle} at ${companyName}${location ? ` in ${location}` : ""}. Let's refine the requirements — what's the most important thing you're looking for in this candidate?`
      : `Let's refine your job requirements. What's the most important thing you're looking for in this candidate?`;

    try {
      const vapi = ensureVapi();
      setCallStatus("connecting");
      await vapi.start(assistantId, {
        variableValues: {
          jobTitle,
          companyName,
          jobDescription: jobDescription.slice(0, 500), // keep prompt size reasonable
          location,
          compensation: jobContext.compensation,
          workAuthorization: jobContext.workAuthorization,
          remotePolicy: jobContext.remotePolicy,
          experienceRequired: jobContext.experienceRequired,
          autoExportToAts: String(jobContext.autoExportToAts),
          jobContext: JSON.stringify(jobContext),
          companyContext: JSON.stringify(companyContext),
          recruiterName,
        },
        firstMessage,
      });
    } catch (error) {
      terminalStateRef.current = "error";
      console.error("[voice] start_failed", {
        jobId,
        error,
      });
      setCallStatus("error");
      setPipelineStatus("error");
      setPipelineError(`Unable to start voice session: ${toErrorMessage(error)}`);
    }
  };

  // ── end call manually ──────────────────────────────────────────────────────
  const handleEndCall = async () => {
    if (!vapiRef.current) return;
    terminalStateRef.current = "manual-stop";
    setCallStatus("processing");
    try {
      await vapiRef.current.stop();
    } catch {
      setCallStatus("error");
      setPipelineError("Could not end the call cleanly. Please try again.");
    }
  };

  // ── cleanup on unmount ─────────────────────────────────────────────────────
  useEffect(() => {
    return () => { vapiRef.current?.stop().catch(() => undefined); };
  }, []);

  // ── derived display state ──────────────────────────────────────────────────
  const isIdle = callStatus === "idle";
  const isErrorState = callStatus === "error";
  const isLive = callStatus === "connecting" || callStatus === "listening" || callStatus === "speaking";
  const isSpeaking = callStatus === "speaking";
  const isProcessingCall = callStatus === "processing" || callStatus === "completed";
  const showChat = !isIdle || chatMessages.length > 0;

  const pipelineLabel: Record<typeof pipelineStatus, string> = {
    idle: "",
    refining: "Analysing conversation and updating job profile...",
    fetching: "Running candidate search with updated requirements...",
    done: "Done — loading your candidates.",
    error: pipelineError,
  };

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 overflow-hidden rounded-full">
              <img src="/images/adam.png" alt="Adam" className="h-full w-full object-cover" />
            </div>
            <p className="font-heading text-2xl leading-none text-[#111111]">Adam</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-green-100 px-3 py-1 text-sm font-medium text-green-700">Discovery</span>
            {/* <span className="rounded-full bg-gray-100 px-3 py-1 text-sm font-medium text-gray-500">Calibration</span>
            <span className="rounded-full bg-gray-100 px-3 py-1 text-sm font-medium text-gray-500">Summary</span> */}
          </div>
        </div>

        {isLive && (
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 rounded-full bg-green-100 px-3 py-1">
              <span className="h-2 w-2 animate-pulse rounded-full bg-green-600" />
              <span className="text-sm font-medium text-green-700">
                {isSpeaking ? "Speaking" : "Listening"}
              </span>
            </div>
            <button
              onClick={handleEndCall}
              className="rounded-full border border-red-500 px-4 py-1 text-red-500 hover:bg-red-50"
            >
              End
            </button>
          </div>
        )}
      </div>

      {/* Conversation panel */}
      <div className="rounded-[20px] border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] p-6 shadow-[0_4px_12px_rgba(0,0,0,0.02)] md:p-8">
        <div className="mb-6 flex items-center justify-between gap-4">
          <p className="font-body text-base font-semibold text-[#111111]">Conversation</p>
          {isLive && (
            <p className="font-body text-sm text-[#6B7280]">Say &quot;that&apos;s everything&quot; to finish</p>
          )}
        </div>

        <div className="space-y-6">
          {/* Empty state */}
          {(isIdle || isErrorState) && chatMessages.length === 0 && (
            <div className="flex min-h-[120px] items-center justify-center rounded-xl bg-[#F9FAFB]">
              <p className="font-body text-sm text-[#6B7280]">Click start to begin voice intake</p>
            </div>
          )}

          {/* Chat bubbles */}
          {showChat && (
            <div
              ref={chatScrollRef}
              className="max-h-[320px] space-y-3 overflow-y-auto rounded-xl bg-[#F8FAFC] p-3 md:max-h-[360px]"
            >
              {chatMessages.length === 0 && (callStatus === "connecting" || callStatus === "listening") && (
                <p className="text-center text-xs text-gray-400">Waiting for Adam...</p>
              )}
              {chatMessages.map((msg, i) => (
                <ChatBubble key={`${msg.role}-${i}-${msg.text.slice(0, 12)}`} message={msg} />
              ))}
              {/* "Adam is thinking..." indicator — shown when assistant speech just ended and user hasn't spoken */}
              {callStatus === "listening" && chatMessages[chatMessages.length - 1]?.role === "assistant" && (
                <div className="flex items-center gap-2 px-2 py-1">
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:0ms]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:150ms]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:300ms]" />
                </div>
              )}
            </div>
          )}

          {/* Wave animation while live */}
          <AnimatePresence mode="wait">
            {isLive && (
              <motion.div
                key="wave"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.22 }}
                className="flex min-h-[80px] items-center justify-center"
              >
                <WaveAnimation isActive />
              </motion.div>
            )}
          </AnimatePresence>

          {/* Pipeline status — shown after call ends */}
          {(isProcessingCall || isErrorState) && pipelineStatus !== "idle" && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className={`flex items-center gap-3 rounded-xl px-5 py-4 ${
                pipelineStatus === "error" ? "bg-red-50" : "bg-[#F3F4F6]"
              }`}
            >
              {pipelineStatus !== "error" && pipelineStatus !== "done" && (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-[#1F6F4A]" />
              )}
              {pipelineStatus === "done" && (
                <span className="text-green-600">✓</span>
              )}
              <span className={`font-body text-sm ${pipelineStatus === "error" ? "text-red-600" : "text-[#6B7280]"}`}>
                {pipelineLabel[pipelineStatus]}
              </span>
            </motion.div>
          )}

          {/* Action buttons */}
          <div className="mt-6 flex gap-3">
            {(isIdle || isErrorState) && (
              <button
                onClick={handleStart}
                className="rounded-xl bg-[#1F6F4A] px-6 py-3 font-body text-base font-semibold text-white"
              >
                {isErrorState ? "Start Conversation Again" : "Start Conversation"}
              </button>
            )}

            {/* Retry button on error */}
            {pipelineStatus === "error" && (
              <button
                onClick={() => { void handleStart(); }}
                className="rounded-xl border border-gray-300 px-6 py-3 font-body text-base font-semibold text-gray-700 hover:bg-gray-50"
              >
                Try Again
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
