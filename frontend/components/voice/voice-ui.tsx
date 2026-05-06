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

type TranscriptRole = VoiceTurn["role"];

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

function mergeTranscriptFragments(previous: string, incoming: string): string {
  const prev = normalize(previous);
  const next = normalize(incoming);

  if (!prev) return next;
  if (!next) return prev;
  const prevLower = prev.toLowerCase();
  const nextLower = next.toLowerCase();

  if (nextLower === prevLower) return next;
  if (nextLower.startsWith(prevLower)) return next;
  if (prevLower.startsWith(nextLower)) return prev;
  if (prevLower.includes(nextLower)) return prev;
  if (nextLower.includes(prevLower)) return next;

  const prevWords = prev.split(" ");
  const nextWords = next.split(" ");
  const maxOverlap = Math.min(12, prevWords.length, nextWords.length);

  for (let size = maxOverlap; size > 0; size -= 1) {
    const prevTail = prevWords.slice(-size).join(" ").toLowerCase();
    const nextHead = nextWords.slice(0, size).join(" ").toLowerCase();
    if (prevTail === nextHead) {
      return [...prevWords, ...nextWords.slice(size)].join(" ");
    }
  }

  return `${prev} ${next}`.replace(/\s+/g, " ").trim();
}

function splitCompleteSentences(text: string): { sentences: string[]; remainder: string } {
  const normalized = normalize(text);
  if (!normalized) {
    return { sentences: [], remainder: "" };
  }

  const matches = normalized.match(/[^.!?]+[.!?]+(?:["')\]]+)?/g) || [];
  const consumed = matches.join("").length;
  const remainder = normalize(normalized.slice(consumed));

  return {
    sentences: matches.map(normalize).filter(Boolean),
    remainder,
  };
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

function debugVoice(event: string, details?: Record<string, unknown>) {
  if (details) {
    console.info(`[voice-debug] ${event}`, details);
    return;
  }
  console.info(`[voice-debug] ${event}`);
}

function getRuntimeEnvSnapshot() {
  if (typeof window === "undefined") {
    return {
      runtime: "server",
      origin: null,
      hostname: null,
      href: null,
    };
  }

  return {
    runtime: process.env.NODE_ENV || "unknown",
    origin: window.location.origin,
    hostname: window.location.hostname,
    href: window.location.href,
  };
}

async function loadVapiConfig() {
  const response = await fetch("/api/vapi/config", {
    method: "GET",
    headers: {
      "Content-Type": "application/json"
    }
  });

  if (!response.ok) {
    throw new Error(`Unable to load Vapi config (${response.status})`);
  }

  const payload = (await response.json()) as {
    success?: boolean;
    data?: {
      publicKey?: string;
      assistantId?: string;
      hasPublicKey?: boolean;
      hasAssistantId?: boolean;
    };
    error?: string;
  };

  const publicKey = payload.data?.publicKey?.trim() || "";
  const assistantId = payload.data?.assistantId?.trim() || "";
  return { publicKey, assistantId };
}

// ─── component ────────────────────────────────────────────────────────────────

export function VoiceUi() {
  const router = useRouter();
  const { callStatus, setCallStatus, setVoiceNotes, setCandidates, setIsRefined, jobId, job, company, user } = useAppContext();

  const [finalTranscript, setFinalTranscript] = useState<ChatMessage[]>([]);
  const [interimTranscript, setInterimTranscript] = useState("");
  const [interimRole, setInterimRole] = useState<TranscriptRole | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<"idle" | "refining" | "fetching" | "done" | "error">("idle");
  const [pipelineError, setPipelineError] = useState("");

  // Refs — never cause re-renders, safe to read inside Vapi callbacks
  const vapiRef = useRef<Vapi | null>(null);
  const turnsRef = useRef<VoiceTurn[]>([]);       // full structured conversation
  const firedRef = useRef(false);                  // guard against double pipeline trigger
  const callStartedAtRef = useRef<number | null>(null);
  const terminalStateRef = useRef<"idle" | "starting" | "live" | "manual-stop" | "ejected" | "error" | "done">("idle");
  const transcriptBufferRef = useRef<Record<TranscriptRole, string>>({ assistant: "", user: "" });
  const interimRoleRef = useRef<TranscriptRole | null>(null);
  const interimTranscriptRef = useRef("");
  const transcriptFlushTimersRef = useRef<Record<TranscriptRole, ReturnType<typeof setTimeout> | null>>({
    assistant: null,
    user: null,
  });

  // ── scroll chat to bottom on new messages ──────────────────────────────────
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    chatScrollRef.current?.scrollTo({ top: chatScrollRef.current.scrollHeight, behavior: "smooth" });
  }, [finalTranscript, interimTranscript, interimRole]);

  const clearTranscriptFlushTimer = useCallback((role: TranscriptRole) => {
    const timer = transcriptFlushTimersRef.current[role];
    if (timer) {
      clearTimeout(timer);
      transcriptFlushTimersRef.current[role] = null;
    }
  }, []);

  const clearLiveTranscript = useCallback((role?: TranscriptRole) => {
    if (role && interimRoleRef.current !== role) {
      return;
    }
    interimRoleRef.current = null;
    interimTranscriptRef.current = "";
    setInterimRole(null);
    setInterimTranscript("");
  }, []);

  const setLiveTranscript = useCallback((role: TranscriptRole, text: string) => {
    const normalized = normalize(text);
    if (!normalized) {
      clearLiveTranscript(role);
      return;
    }

    interimRoleRef.current = role;
    interimTranscriptRef.current = normalized;
    setInterimRole(role);
    setInterimTranscript(normalized);
  }, [clearLiveTranscript]);

  const appendFinalSentence = useCallback((role: TranscriptRole, text: string) => {
    const normalized = normalize(text);
    if (!normalized) return;

    setFinalTranscript((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      const lastText = last ? normalize(last.text).toLowerCase() : "";
      const nextText = normalized.toLowerCase();

      if (last?.role === role) {
        if (lastText === nextText) {
          return prev;
        }
        if (nextText.startsWith(lastText) || lastText.startsWith(nextText)) {
          next[next.length - 1] = { role, text: normalized, isFinal: true };
          return next;
        }
      }

      next.push({ role, text: normalized, isFinal: true });
      return next;
    });

    turnsRef.current = (() => {
      const nextTurns = [...turnsRef.current];
      const lastTurn = nextTurns[nextTurns.length - 1];
      const lastText = lastTurn ? normalize(lastTurn.text).toLowerCase() : "";
      const nextText = normalized.toLowerCase();

      if (lastTurn?.role === role) {
        if (lastText === nextText) {
          return nextTurns;
        }
        if (nextText.startsWith(lastText) || lastText.startsWith(nextText)) {
          nextTurns[nextTurns.length - 1] = { role, text: normalized };
          return nextTurns;
        }
      }

      nextTurns.push({ role, text: normalized });
      return nextTurns;
    })();
  }, []);

  const finalizeBufferedTranscript = useCallback((role: TranscriptRole, forceFinalize = false) => {
    const buffered = normalize(transcriptBufferRef.current[role]);
    clearTranscriptFlushTimer(role);

    if (!buffered) {
      transcriptBufferRef.current[role] = "";
      clearLiveTranscript(role);
      return;
    }

    if (forceFinalize) {
      appendFinalSentence(role, buffered);
      transcriptBufferRef.current[role] = "";
      clearLiveTranscript(role);
      return;
    }

    const { sentences, remainder } = splitCompleteSentences(buffered);
    if (sentences.length > 0) {
      sentences.forEach((sentence) => appendFinalSentence(role, sentence));
      transcriptBufferRef.current[role] = remainder;
      if (remainder) {
        setLiveTranscript(role, remainder);
        transcriptFlushTimersRef.current[role] = setTimeout(() => {
          finalizeBufferedTranscript(role, true);
        }, 800);
      } else {
        clearLiveTranscript(role);
      }
      return;
    }

    setLiveTranscript(role, buffered);
    transcriptFlushTimersRef.current[role] = setTimeout(() => {
      finalizeBufferedTranscript(role, true);
    }, 800);
  }, [appendFinalSentence, clearLiveTranscript, clearTranscriptFlushTimer, setLiveTranscript]);

  const processTranscriptEvent = useCallback((event: { role: TranscriptRole; text: string; isFinal: boolean }) => {
    const role = event.role;
    const merged = mergeTranscriptFragments(transcriptBufferRef.current[role], event.text);
    transcriptBufferRef.current[role] = merged;
    setLiveTranscript(role, merged);

    if (event.isFinal) {
      finalizeBufferedTranscript(role, true);
      return;
    }

    finalizeBufferedTranscript(role, false);
  }, [finalizeBufferedTranscript, setLiveTranscript]);

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
  const ensureVapi = useCallback((publicKey: string) => {
    if (vapiRef.current) return vapiRef.current;

    debugVoice("ensureVapi called", {
      hasPublicKey: Boolean(publicKey),
      publicKeyPreview: publicKey ? `${publicKey.slice(0, 6)}...${publicKey.slice(-4)}` : null,
    });
    if (!publicKey) throw new Error("NEXT_PUBLIC_VAPI_PUBLIC_KEY is not set.");

    const vapi = new Vapi(publicKey);
    debugVoice("vapi instance created");

    if (typeof window !== "undefined") {
      (window as Window & { vapi?: Vapi }).vapi = vapi;
      console.log("[vapi] instance attached to window.vapi");
    }

    vapi.on("call-start", () => {
      terminalStateRef.current = "live";
      if (!callStartedAtRef.current) {
        callStartedAtRef.current = Date.now();
      }
      debugVoice("call-start", {
        jobId,
      });
      setCallStatus("listening");
    });

    vapi.on("speech-start", () => {
      debugVoice("speech-start");
      setCallStatus("speaking");
    });
    vapi.on("speech-end", () => {
      debugVoice("speech-end");
      setCallStatus("listening");
    });

    vapi.on("message", (message) => {
      debugVoice("message received", {
        type: typeof message === "object" && message ? String((message as Record<string, unknown>).type || "") : typeof message,
      });
      const event = extractTranscriptEvent(message);
      if (!event) return;

      processTranscriptEvent(event);
    });

    vapi.on("error", (error) => {
      const classified = classifyVoiceError(error);
      terminalStateRef.current = classified.kind === "ejected" ? "ejected" : "error";
      const endedAt = Date.now();
      debugVoice("error", {
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
      debugVoice("call-start-failed", {
        jobId,
        event,
      });
      setCallStatus("error");
      setPipelineStatus("error");
      setPipelineError(`Unable to start voice session: ${event?.error || "unknown startup failure"}`);
    });

    vapi.on("call-end", () => {
      const endedAt = Date.now();
      debugVoice("call-end", {
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
      clearTranscriptFlushTimer("assistant");
      clearTranscriptFlushTimer("user");
      finalizeBufferedTranscript("assistant", true);
      finalizeBufferedTranscript("user", true);
      // Auto-trigger pipeline with everything captured so far
      void runPipeline(turnsRef.current);
    });

    vapiRef.current = vapi;
    return vapi;
  }, [finalizeBufferedTranscript, runPipeline, setCallStatus]);

  // ── start call ─────────────────────────────────────────────────────────────
  const handleStart = async () => {
    debugVoice("start button clicked", {
      hasJobId: Boolean(jobId),
      hasJobTitle: Boolean(job.title),
      hasCompany: Boolean(company.name),
    });
    debugVoice("runtime snapshot", getRuntimeEnvSnapshot());
    let assistantId = process.env.NEXT_PUBLIC_VAPI_ASSISTANT_ID;
    let publicKey = process.env.NEXT_PUBLIC_VAPI_PUBLIC_KEY;
    debugVoice("env snapshot", {
      hasAssistantId: Boolean(assistantId),
      hasPublicKey: Boolean(publicKey),
      assistantIdPreview: assistantId ? `${assistantId.slice(0, 6)}...${assistantId.slice(-4)}` : null,
      publicKeyPreview: publicKey ? `${publicKey.slice(0, 6)}...${publicKey.slice(-4)}` : null,
    });

    if (!assistantId || !publicKey) {
      try {
        const runtimeConfig = await loadVapiConfig();
        assistantId = assistantId || runtimeConfig.assistantId;
        publicKey = publicKey || runtimeConfig.publicKey;
        debugVoice("runtime vapi config loaded", {
          hasAssistantId: Boolean(assistantId),
          hasPublicKey: Boolean(publicKey),
          assistantIdPreview: assistantId ? `${assistantId.slice(0, 6)}...${assistantId.slice(-4)}` : null,
          publicKeyPreview: publicKey ? `${publicKey.slice(0, 6)}...${publicKey.slice(-4)}` : null,
        });
      } catch (error) {
        debugVoice("runtime vapi config failed", { error });
      }
    }

    if (!assistantId) {
      setCallStatus("error");
      setPipelineError("Voice assistant not configured. Add NEXT_PUBLIC_VAPI_ASSISTANT_ID.");
      debugVoice("start aborted", { reason: "missing assistantId" });
      return;
    }
    if (!publicKey) {
      setCallStatus("error");
      setPipelineError("Voice public key not configured. Add NEXT_PUBLIC_VAPI_PUBLIC_KEY.");
      debugVoice("start aborted", { reason: "missing publicKey" });
      return;
    }

    // Reset state
    setFinalTranscript([]);
    setInterimTranscript("");
    setInterimRole(null);
    interimRoleRef.current = null;
    interimTranscriptRef.current = "";
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
      const vapi = ensureVapi(publicKey);
      setCallStatus("connecting");
      debugVoice("calling vapi.start", {
        assistantIdPreview: `${assistantId.slice(0, 6)}...${assistantId.slice(-4)}`,
        jobId,
      });
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
      debugVoice("vapi.start resolved");
    } catch (error) {
      terminalStateRef.current = "error";
      debugVoice("start_failed", {
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
      finalizeBufferedTranscript("assistant", true);
      finalizeBufferedTranscript("user", true);
      await vapiRef.current.stop();
    } catch {
      setCallStatus("error");
      setPipelineError("Could not end the call cleanly. Please try again.");
    }
  };

  // ── cleanup on unmount ─────────────────────────────────────────────────────
  useEffect(() => {
    return () => {
      vapiRef.current?.stop().catch(() => undefined);
      clearTranscriptFlushTimer("assistant");
      clearTranscriptFlushTimer("user");
      clearLiveTranscript();
    };
  }, [clearLiveTranscript, clearTranscriptFlushTimer]);

  // ── derived display state ──────────────────────────────────────────────────
  const isIdle = callStatus === "idle";
  const isErrorState = callStatus === "error";
  const isLive = callStatus === "connecting" || callStatus === "listening" || callStatus === "speaking";
  const isSpeaking = callStatus === "speaking";
  const isProcessingCall = callStatus === "processing" || callStatus === "completed";
  const showChat = !isIdle || finalTranscript.length > 0 || Boolean(interimTranscript);

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
            <span className="rounded-full bg-gray-100 px-3 py-1 text-sm font-medium text-gray-500">Calibration</span>
            <span className="rounded-full bg-gray-100 px-3 py-1 text-sm font-medium text-gray-500">Summary</span>
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
          {(isIdle || isErrorState) && finalTranscript.length === 0 && !interimTranscript && (
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
              {finalTranscript.length === 0 && !interimTranscript && (callStatus === "connecting" || callStatus === "listening") && (
                <p className="text-center text-xs text-gray-400">Waiting for Adam...</p>
              )}
              {finalTranscript.map((msg, i) => (
                <ChatBubble key={`${msg.role}-${i}-${msg.text.slice(0, 12)}`} message={msg} />
              ))}
              {interimTranscript && interimRole && (
                <motion.div
                  key={`${interimRole}-live-caption`}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={{ duration: 0.16 }}
                  className="space-y-2 pt-2"
                >
                  <p className="px-2 text-[11px] uppercase tracking-[0.18em] text-gray-400">Live caption</p>
                  <ChatBubble message={{ role: interimRole, text: interimTranscript, isFinal: false }} isInterim />
                </motion.div>
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
