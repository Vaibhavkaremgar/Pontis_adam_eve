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
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import Vapi from "@vapi-ai/web";

import { useAppContext } from "@/context/AppContext";
import { getCandidatesWithMode } from "@/lib/api/candidates";
import { getRecruiterIntelligence, updateRecruiterIntelligence } from "@/lib/api/recruiter-intelligence";
import { refineWithVoice } from "@/lib/api/voice";
import type { RecruiterIntelligenceSession } from "@/lib/api/recruiter-intelligence";

import { WaveAnimation } from "./wave-animation";

// ─── types ────────────────────────────────────────────────────────────────────

type VoiceTurn = {
  role: "assistant" | "user";
  text: string;
};

type TranscriptRole = VoiceTurn["role"];
type StreamingMessage = {
  id: string;
  role: "assistant" | "user";
  speaker: "Adam" | "Recruiter";
  content: string;
  isStreaming: boolean;
  isFinal: boolean;
  timestamp: string;
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

function speakerLabel(role: TranscriptRole): "Adam" | "Recruiter" {
  return role === "assistant" ? "Adam" : "Recruiter";
}

function createMessageId(role: TranscriptRole, suffix = "") {
  const base = `${role}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  return suffix ? `${base}-${suffix}` : base;
}

function transcriptRoleMeta(role: TranscriptRole) {
  if (role === "assistant") {
    return {
      name: "Adam",
      accent: "text-[#166534]",
      pill: "border-[#D1FAE5] bg-[#ECFDF5] text-[#166534]",
      card: "border-[#E7E0D4] bg-white text-[#111827]",
      meta: "text-[#6B7280]",
    };
  }

  return {
    name: "Recruiter",
    accent: "text-white",
    pill: "border-[#4CAF7A] bg-[#0F6B3A] text-white",
    card: "border-[#0F6B3A] bg-[#0F6B3A] text-white",
    meta: "text-white/70",
  };
}

function upsertTurn(turns: VoiceTurn[], role: TranscriptRole, text: string, isFinal = false): VoiceTurn[] {
  const normalized = normalize(text);
  if (!normalized) return turns;

  const next = [...turns];
  const last = next[next.length - 1];
  if (last?.role === role) {
    next[next.length - 1] = {
      role,
      text: accumulateTranscript(last.text, normalized, isFinal),
    };
    return next;
  }

  next.push({ role, text: normalized });
  return next;
}

function extractTranscriptEvent(message: unknown): { role: "assistant" | "user"; text: string; isFinal: boolean } | null {
  if (!message || typeof message !== "object") return null;
  const r = message as Record<string, unknown>;
  if (r.type !== "transcript") return null;
  if (typeof r.transcript !== "string") return null;
  const text = normalize(r.transcript);
  if (!text) return null;
  const role: "assistant" | "user" = r.role === "assistant" ? "assistant" : "user";
  const isFinal = r.transcriptType === "final" || r.isFinal === true || r.final === true;
  return { role, text, isFinal };
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

const AUTO_CLOSE_PHRASES = [
  "that's fine",
  "thats fine",
  "we are good to go",
  "we're good to go",
  "we're good",
  "we are good",
  "that is everything",
  "that's everything",
  "that is all",
  "that's all",
  "no that's all",
  "no thanks that's all",
  "nothing else",
  "nothing more",
];

function shouldAutoCloseCall(text: string): boolean {
  const normalized = normalize(text).toLowerCase();
  if (!normalized) return false;
  if (/\b(not|nope|don't|do not)\b/.test(normalized)) return false;
  return AUTO_CLOSE_PHRASES.some((phrase) => normalized.includes(phrase));
}

function normalizeFinalTranscriptText(text: string): string {
  const normalized = normalize(text);
  if (!normalized) return "";

  const withCompactSpacing = normalized.replace(/\s+/g, " ");
  const cleaned = withCompactSpacing
    .replace(/\s+([,.;:!?])/g, "$1")
    .replace(/([,.;:!?])(?!\s|$)/g, "$1 ")
    .replace(/\s+/g, " ")
    .trim();

  const sentence = cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
  return sentence
    .replace(/\b(and)\s+and\b/gi, "and")
    .replace(/\b(,)\s+(,)+/g, ",")
    .replace(/\s+,/g, ",")
    .replace(/,\s+and\s+and\b/gi, ", and")
    .replace(/\s+/g, " ")
    .trim();
}

function mergeASRRevision(previous: string, incoming: string): string {
  const prev = normalize(previous);
  const next = normalize(incoming);
  if (!prev) return next;
  if (!next) return prev;
  if (next.toLowerCase() === prev.toLowerCase()) return prev;
  if (next.toLowerCase().startsWith(prev.toLowerCase())) return next;
  if (prev.toLowerCase().startsWith(next.toLowerCase())) return prev;
  if (prev.toLowerCase().includes(next.toLowerCase())) return prev;
  if (next.toLowerCase().includes(prev.toLowerCase())) return next;

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

  return next;
}

function accumulateTranscript(previous: string, incoming: string, isFinal = false): string {
  return isFinal ? mergeTranscriptEventContent(previous, incoming, true) : mergeTranscriptText(previous, incoming);
}

function mergeTranscriptText(previous: string, incoming: string): string {
  const prev = normalize(previous);
  const next = normalize(incoming);
  if (!prev) return next;
  if (!next) return prev;

  const prevLower = prev.toLowerCase();
  const nextLower = next.toLowerCase();

  if (nextLower === prevLower) return prev;
  if (nextLower.startsWith(prevLower)) return next;
  if (prevLower.startsWith(nextLower)) return prev;
  if (prevLower.includes(nextLower)) return prev;
  if (nextLower.includes(prevLower)) return next;

  const revised = mergeASRRevision(prev, next);
  if (revised.toLowerCase() === prevLower) return prev;
  if (revised.toLowerCase() === nextLower) return next;
  if (revised && revised.length >= prev.length && revised.toLowerCase().includes(prevLower)) {
    return revised;
  }

  const separator = /[.!?]$/.test(prev) ? " " : ". ";
  return `${prev}${separator}${next}`.replace(/\s+/g, " ").trim();
}

function mergeTranscriptEventContent(previous: string, incoming: string, isFinal: boolean): string {
  const mergedContent = mergeTranscriptText(previous, incoming);
  const candidateContent = isFinal ? normalizeFinalTranscriptText(mergedContent) : mergedContent;
  if (!candidateContent) return "";

  const prev = normalize(previous);
  return prev && candidateContent.length < prev.length && !isFinal ? prev : candidateContent;
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
  const { callStatus, setCallStatus, setVoiceNotes, setCandidates, setIsRefined, jobId, job, company, user, isSessionReady } = useAppContext();

  const [finalTranscript, setFinalTranscript] = useState<StreamingMessage[]>([]);
  const [pipelineStatus, setPipelineStatus] = useState<"idle" | "refining" | "fetching" | "done" | "error">("idle");
  const [pipelineError, setPipelineError] = useState("");
  const [intelligence, setIntelligence] = useState<RecruiterIntelligenceSession | null>(null);
  const [intelligenceLoading, setIntelligenceLoading] = useState(false);

  // Refs — never cause re-renders, safe to read inside Vapi callbacks
  const vapiRef = useRef<Vapi | null>(null);
  const turnsRef = useRef<VoiceTurn[]>([]);       // full structured conversation
  const firedRef = useRef(false);                  // guard against double pipeline trigger
  const callStartedAtRef = useRef<number | null>(null);
  const autoEndTimerRef = useRef<number | null>(null);
  const terminalStateRef = useRef<"idle" | "starting" | "live" | "manual-stop" | "ejected" | "error" | "done">("idle");

  useEffect(() => {
    if (!isSessionReady || !user || !jobId) return;

    let cancelled = false;
    const loadIntelligence = async () => {
      setIntelligenceLoading(true);
      const result = await getRecruiterIntelligence(user.id, jobId);
      if (!cancelled && result.success && result.data) {
        setIntelligence(result.data);
      }
      if (!cancelled) {
        setIntelligenceLoading(false);
      }
    };

    void loadIntelligence();
    return () => {
      cancelled = true;
    };
  }, [isSessionReady, jobId, user]);

  // ── scroll chat to bottom on new messages ──────────────────────────────────
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    chatScrollRef.current?.scrollTo({ top: chatScrollRef.current.scrollHeight, behavior: "smooth" });
  }, [finalTranscript]);
  const selectionMood = job.vettingMode || "volume";
  const fullTranscriptText = turnsRef.current.length ? buildFullTranscript(turnsRef.current) : "";
  const callStatusLabel: Record<string, string> = {
    idle: "Ready to start voice intake.",
    connecting: "Connecting to voice assistant...",
    speaking: "Adam is speaking.",
    listening: "Listening to the recruiter.",
    processing: "Finalizing the transcript and refining candidates.",
    completed: "Conversation completed.",
    error: "There was a problem capturing the transcript.",
  };

  const upsertStreamingMessage = useCallback((role: TranscriptRole, text: string, isFinal: boolean) => {
    const speaker = speakerLabel(role);
    const timestamp = new Date().toISOString();
    const incoming = normalize(text);
    if (!incoming) return;

    setFinalTranscript((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      const normalizedContent = last && last.role === role
        ? mergeTranscriptEventContent(last.content, incoming, isFinal)
        : mergeTranscriptEventContent("", incoming, isFinal);
      if (!normalizedContent) return prev;

      // Keep every speaker turn as a single bubble by merging same-speaker fragments.
      if (last && last.role === role) {
        next[next.length - 1] = {
          ...last,
          speaker,
          content: normalizedContent,
          isStreaming: !isFinal,
          isFinal,
          timestamp,
        };
        return next;
      }

      if (last && last.isStreaming) {
        next[next.length - 1] = {
          ...last,
          isStreaming: false,
          isFinal: true,
          timestamp,
        };
      }

      return [
        ...next,
        {
          id: createMessageId(role),
          role,
          speaker,
          content: normalizedContent,
          isStreaming: !isFinal,
          isFinal,
          timestamp,
        },
      ];
    });

    turnsRef.current = upsertTurn(turnsRef.current, role, incoming, isFinal);
  }, []);

  const requestCallStop = useCallback(async () => {
    const vapi = vapiRef.current;
    if (!vapi) return;
    if (autoEndTimerRef.current !== null) {
      window.clearTimeout(autoEndTimerRef.current);
      autoEndTimerRef.current = null;
    }
    terminalStateRef.current = "manual-stop";
    setCallStatus("processing");
    try {
      await vapi.stop();
    } catch {
      setCallStatus("error");
      setPipelineError("Could not end the call cleanly. Please try again.");
    }
  }, [setCallStatus]);

  const processTranscriptEvent = useCallback((event: { role: TranscriptRole; text: string; isFinal: boolean }) => {
    upsertStreamingMessage(event.role, event.text, event.isFinal);
    if (event.role === "user" && event.isFinal && shouldAutoCloseCall(event.text)) {
      if (autoEndTimerRef.current !== null) {
        window.clearTimeout(autoEndTimerRef.current);
      }
      autoEndTimerRef.current = window.setTimeout(() => {
        if (terminalStateRef.current === "live") {
          void requestCallStop();
        }
      }, 700);
    }
  }, [requestCallStop, upsertStreamingMessage]);

  // ── pipeline: refine → fetch candidates → navigate ─────────────────────────
  const runPipeline = useCallback(async (turns: VoiceTurn[]) => {
    if (firedRef.current) return;
    firedRef.current = true;

    const fullTranscript = buildFullTranscript(turns);
    const cleanedTranscript = turns
      .map((turn) => `${turn.role === "assistant" ? "Adam" : "Recruiter"}: ${normalizeFinalTranscriptText(turn.text)}`)
      .join("\n");
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
    setVoiceNotes([cleanedTranscript || fullTranscript]);

    if (user && jobId) {
      const intelligenceResult = await updateRecruiterIntelligence(user.id, jobId, {
        jobId,
        transcript: cleanedTranscript || fullTranscript,
        voiceSummary: cleanedTranscript || fullTranscript,
        entities: {},
      });
      if (intelligenceResult.success && intelligenceResult.data) {
        setIntelligence(intelligenceResult.data);
      }
    }

    setPipelineStatus("refining");
    const refineResult = await refineWithVoice({
      jobId,
      voiceNotes: [cleanedTranscript || fullTranscript],
      transcript: cleanedTranscript || fullTranscript,
    });

    if (!refineResult.success) {
      setPipelineStatus("error");
      setPipelineError(refineResult.error || "Could not refine job. Proceeding with original.");
      // Soft failure — still fetch candidates with original job
    }

    setPipelineStatus("fetching");
    const candidatesResult = await getCandidatesWithMode({
      jobId,
      mode: selectionMood,
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
  }, [jobId, router, selectionMood, setCandidates, setIsRefined, setVoiceNotes, user]);

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
      setFinalTranscript((prev) => {
        if (prev.length === 0) return prev;
        const next = [...prev];
        const lastIndex = next.length - 1;
        const last = next[lastIndex];
        if (last && last.isStreaming) {
          next[lastIndex] = {
            ...last,
            isStreaming: false,
            isFinal: true,
            timestamp: new Date().toISOString(),
          };
        }
        return next;
      });
      // Auto-trigger pipeline with everything captured so far
      void runPipeline(turnsRef.current);
    });

    vapiRef.current = vapi;
    return vapi;
  }, [jobId, processTranscriptEvent, runPipeline, setCallStatus]);

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
      vettingMode: selectionMood,
    };
    const companyContext = {
      name: company.name || "",
      website: company.website || "",
      description: company.description || "",
      industry: company.industry || "",
      atsProvider: company.atsProvider || "",
      atsConnected: Boolean(company.atsConnected),
    };

    const interviewQuestions = intelligence?.interview?.recommended_questions || intelligence?.selection?.recommended_questions || [];
    const firstQuestion = intelligence?.interview?.current_question || interviewQuestions[0] || "What's the most important thing you're looking for in this candidate?";
    const questionList = interviewQuestions.length
      ? interviewQuestions.map((question, index) => `${index + 1}. ${question}`).join("\n")
      : firstQuestion;
    const firstMessage = companyName && jobTitle
      ? `You're hiring a ${jobTitle} at ${companyName}${location ? ` in ${location}` : ""}. Let's focus on this first: ${firstQuestion}. At the end, I'll summarize the intake, ask whether you want to add anything else, thank you for the input, and close the call once you confirm we're good.`
      : `Let's refine your job requirements. ${firstQuestion}. I'll summarize what I captured, ask if you'd like to add anything, and then close the call once you confirm we're good.`;
    const closingInstructions = [
      "When the recruiter confirms the intake is complete, acknowledge it briefly, say thanks for the input, and end the call.",
      "If the recruiter wants to add anything else, capture it first before closing.",
      "If the recruiter says 'that's fine', 'we're good to go', 'good to go', 'nothing else', or similar confirmation, end the call right after thanking them.",
    ].join(" ");

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
          vettingMode: selectionMood,
          candidateMood: selectionMood,
          dynamicQuestions: questionList,
          dynamicQuestionCount: String(interviewQuestions.length || 1),
          firstDynamicQuestion: firstQuestion,
          jobContext: JSON.stringify(jobContext),
          companyContext: JSON.stringify(companyContext),
          recruiterName,
          closingInstructions,
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
    void requestCallStop();
  };

  // ── cleanup on unmount ─────────────────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (autoEndTimerRef.current !== null) {
        window.clearTimeout(autoEndTimerRef.current);
        autoEndTimerRef.current = null;
      }
      vapiRef.current?.stop().catch(() => undefined);
    };
  }, []);

  // ── derived display state ──────────────────────────────────────────────────
  const isIdle = callStatus === "idle";
  const isErrorState = callStatus === "error";
  const isLive = callStatus === "connecting" || callStatus === "listening" || callStatus === "speaking";
  const isSpeaking = callStatus === "speaking";
  const isProcessingCall = callStatus === "processing" || callStatus === "completed";
  const showChat = !isIdle || finalTranscript.length > 0;

  const pipelineLabel: Record<typeof pipelineStatus, string> = {
    idle: "",
    refining: "Analysing conversation and updating job profile...",
    fetching: "Running candidate search with updated requirements...",
    done: "Done — loading your candidates.",
    error: pipelineError,
  };

  return (
    <div className="space-y-8">
      <div className="rounded-[28px] border border-gray-200 bg-white p-6 shadow-sm md:p-8">
        <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="space-y-2">
            <p className="font-heading text-2xl font-semibold text-[#111827]">Live voice intake</p>
            <p className="max-w-2xl text-sm text-[#6B7280]">
              This panel displays every speaker turn as a single bubble. The raw transcript below preserves the full spoken text for review and processing.
            </p>
          </div>
          {isLive && (
            <div className="flex flex-wrap items-center gap-3 rounded-full bg-green-50 px-4 py-2 text-sm text-green-700">
              <span className="h-2.5 w-2.5 rounded-full bg-green-600 animate-pulse" />
              {isSpeaking ? "Adam is speaking" : "Listening to the recruiter"}
            </div>
          )}
        </div>

        <div className="grid gap-6 lg:grid-cols-[1.8fr_0.9fr]">
          <div className="space-y-4">
            <div className="rounded-3xl border border-[#E5E7EB] bg-[#F8FAFC] p-4 shadow-sm">
              <div className="mb-4 flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold uppercase tracking-[0.18em] text-[#4B5563]">Transcript feed</p>
                  <p className="text-xs text-[#6B7280]">Live updates from the call, one speaker bubble at a time.</p>
                </div>
                {isLive && <span className="rounded-full bg-[#ECFDF5] px-3 py-1 text-xs font-medium text-[#166534]">Live</span>}
              </div>

              <div ref={chatScrollRef} className="min-h-[280px] max-h-[600px] space-y-4 overflow-y-auto pr-2">
                {finalTranscript.length === 0 && !isLive && (
                  <div className="rounded-3xl border border-dashed border-[#D6D6D6] bg-white p-8 text-center text-sm text-[#6B7280]">
                    Click start to begin voice intake. Your transcript will appear here as the conversation progresses.
                  </div>
                )}

                {finalTranscript.length === 0 && isLive && (
                  <div className="rounded-3xl border border-dashed border-[#D6D6D6] bg-white p-8 text-center text-sm text-[#6B7280]">
                    Listening for audio. Speech will appear as each turn is recognized.
                  </div>
                )}

                {finalTranscript.length > 0 && finalTranscript.map((msg, i) => (
                  <div key={msg.id || `${msg.role}-${i}`} className="rounded-3xl border border-[#E5E7EB] bg-white p-4 shadow-sm">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs uppercase tracking-[0.18em] text-[#6B7280]">
                      <span className="font-semibold">{msg.speaker}</span>
                      <time>{new Date(msg.timestamp).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time>
                    </div>
                    <p className="whitespace-pre-wrap break-words text-sm leading-7 text-[#111827]">
                      {msg.content}
                      {msg.isStreaming && <span className="ml-1 inline-block align-middle text-[#6B7280]">|</span>}
                    </p>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-3xl border border-[#E5E7EB] bg-white p-4 shadow-sm">
              <p className="mb-3 text-sm font-semibold text-[#111827]">Raw transcript</p>
              <div className="min-h-[120px] rounded-3xl border border-[#E5E7EB] bg-[#F9FAFB] p-4 text-sm leading-6 text-[#374151]">
                {fullTranscriptText ? (
                  <pre className="whitespace-pre-wrap break-words text-sm">{fullTranscriptText}</pre>
                ) : (
                  <p className="text-[#6B7280]">No transcript captured yet. Start the call to begin listening.</p>
                )}
              </div>
            </div>
          </div>

          <div className="space-y-4">
            <div className="rounded-3xl border border-[#E5E7EB] bg-[#F8FAFC] p-5 shadow-sm">
              <p className="mb-2 text-sm font-semibold uppercase tracking-[0.18em] text-[#4B5563]">Current status</p>
              <p className="text-sm text-[#111827]">{callStatusLabel[callStatus] || "Ready to capture voice."}</p>
            </div>

            <div className="rounded-3xl border border-[#E5E7EB] bg-white p-5 shadow-sm">
              <p className="mb-3 text-sm font-semibold uppercase tracking-[0.18em] text-[#4B5563]">Call controls</p>
              <div className="flex flex-col gap-3">
                {(isIdle || isErrorState) && (
                  <button
                    onClick={handleStart}
                    className="rounded-2xl bg-[#1F6F4A] px-5 py-3 text-sm font-semibold text-white hover:bg-[#184E3C]"
                  >
                    {isErrorState ? "Retry voice intake" : "Start voice intake"}
                  </button>
                )}
                {isLive && (
                  <button
                    onClick={handleEndCall}
                    className="rounded-2xl border border-red-500 px-5 py-3 text-sm font-semibold text-red-600 hover:bg-red-50"
                  >
                    End call now
                  </button>
                )}
                {pipelineStatus === "error" && (
                  <button
                    onClick={() => { void handleStart(); }}
                    className="rounded-2xl border border-gray-300 px-5 py-3 text-sm font-semibold text-gray-700 hover:bg-gray-50"
                  >
                    Try again
                  </button>
                )}
              </div>
            </div>

            {(isProcessingCall || pipelineStatus !== "idle") && (
              <div className={`rounded-3xl p-4 text-sm ${pipelineStatus === "error" ? "bg-red-50 text-red-700" : "bg-[#F3F4F6] text-[#374151]"}`}>
                <div className="flex items-center gap-2">
                  {pipelineStatus !== "error" && pipelineStatus !== "done" ? (
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-gray-300 border-t-[#1F6F4A]" />
                  ) : null}
                  {pipelineStatus === "done" ? <span className="text-green-600">✓</span> : null}
                  <span>{pipelineLabel[pipelineStatus]}</span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
