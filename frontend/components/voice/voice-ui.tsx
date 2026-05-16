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
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import Vapi from "@vapi-ai/web";

import { useAppContext } from "@/context/AppContext";
import { getCandidatesWithMode } from "@/lib/api/candidates";
import { getRecruiterIntelligence, updateRecruiterIntelligence } from "@/lib/api/recruiter-intelligence";
import { refineWithVoice } from "@/lib/api/voice";
import type { RecruiterIntelligenceSession } from "@/lib/api/recruiter-intelligence";

import { ChatBubble } from "./chat-bubble";
import { WaveAnimation } from "./wave-animation";

// ─── types ────────────────────────────────────────────────────────────────────

type VoiceTurn = {
  role: "assistant" | "user";
  text: string;
};

type TranscriptRole = VoiceTurn["role"];
type TranscriptTurn = {
  id: string;
  role: "assistant" | "user";
  speaker: "Adam" | "Recruiter";
  finalTranscript: string;
  liveTranscript: string;
  isStreaming: boolean;
  isFinal: boolean;
  timestamp: string;
};

// ─── helpers ──────────────────────────────────────────────────────────────────

function normalize(value: string) {
  return value.trim().replace(/\s+/g, " ");
}

function comparisonToken(token: string) {
  return token.replace(/^[^\w]+|[^\w]+$/g, "").toLowerCase();
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

function buildFullTranscript(turns: TranscriptTurn[]): string {
  return turns
    .map((t) => `${t.role === "assistant" ? "Adam" : "Recruiter"}: ${cleanTranscript(t.finalTranscript)}`)
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
      text: normalized,
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

function collapseRepeatedPhrases(text: string): string {
  const normalized = normalize(text);
  if (!normalized) return "";

  const tokens = normalized.split(" ");
  if (tokens.length < 4) return normalized;

  let changed = true;
  while (changed && tokens.length >= 4) {
    changed = false;
    const maxWindow = Math.min(12, Math.floor(tokens.length / 2));
    for (let window = maxWindow; window >= 2; window -= 1) {
      const limit = tokens.length - window * 2 + 1;
      for (let start = 0; start < limit; start += 1) {
        const first = tokens.slice(start, start + window).map(comparisonToken).join(" ");
        const second = tokens.slice(start + window, start + window * 2).map(comparisonToken).join(" ");
        if (first !== second) {
          continue;
        }
        tokens.splice(start + window, window);
        changed = true;
        break;
      }
      if (changed) break;
    }
  }

  return tokens.join(" ").replace(/\s+/g, " ").trim();
}

function dedupeConsecutiveWords(text: string): string {
  const normalized = normalize(text);
  if (!normalized) return "";

  const tokens = normalized.split(" ");
  const dedupedWords: string[] = [];
  for (const token of tokens) {
    const last = dedupedWords[dedupedWords.length - 1];
    if (last && last.toLowerCase() === token.toLowerCase()) {
      continue;
    }
    dedupedWords.push(token);
  }

  return dedupedWords.join(" ").replace(/\s+/g, " ").trim();
}

function collapseRepeatedClauses(text: string): string {
  const normalized = normalize(text);
  if (!normalized) return "";

  const clauses = normalized.match(/[^.!?\n]+[.!?]?/g) ?? [normalized];
  const kept: string[] = [];

  for (const rawClause of clauses) {
    const clause = collapseRepeatedPhrases(rawClause).replace(/\s+/g, " ").trim();
    if (!clause) continue;

    const clauseCore = clause.replace(/[.!?]+$/g, "").toLowerCase();
    if (!clauseCore) continue;

    const last = kept[kept.length - 1];
    if (!last) {
      kept.push(clause);
      continue;
    }

    const lastCore = last.replace(/[.!?]+$/g, "").toLowerCase();
    if (!lastCore) {
      kept[kept.length - 1] = clause;
      continue;
    }

    if (lastCore === clauseCore) {
      continue;
    }

    if (lastCore.includes(clauseCore) && last.length >= clause.length) {
      continue;
    }

    if (clauseCore.includes(lastCore) && clause.length > last.length) {
      kept[kept.length - 1] = clause;
      continue;
    }

    const lastWords = lastCore.split(" ");
    const clauseWords = clauseCore.split(" ");
    const overlap = Math.min(8, lastWords.length, clauseWords.length);
    let merged = false;

    for (let size = overlap; size >= 3; size -= 1) {
      const lastTail = lastWords.slice(-size).join(" ");
      const clauseHead = clauseWords.slice(0, size).join(" ");
      if (lastTail === clauseHead) {
        if (clause.length > last.length) {
          kept[kept.length - 1] = clause;
        }
        merged = true;
        break;
      }
    }

    if (!merged) {
      kept.push(clause);
    }
  }

  return kept.join(" ").replace(/\s+/g, " ").trim();
}

function cleanTranscript(text: string): string {
  const collapsed = collapseRepeatedClauses(text);
  const deduped = dedupeConsecutiveWords(collapsed);
  return normalizeFinalTranscriptText(deduped);
}

function cleanVoiceTranscriptText(text: string): string {
  return cleanTranscript(text);
}

function appendCommittedTranscript(previous: string, incoming: string): string {
  const prev = normalize(previous);
  const next = normalize(incoming);
  if (!prev) return cleanTranscript(next);
  if (!next) return prev;

  const prevLower = prev.toLowerCase();
  const nextLower = next.toLowerCase();
  if (nextLower === prevLower) return prev;
  if (nextLower.startsWith(prevLower)) return cleanTranscript(next);
  if (prevLower.startsWith(nextLower)) return prev;
  if (prevLower.includes(nextLower)) return prev;
  if (nextLower.includes(prevLower)) return next;

  const prevWords = prev.split(" ");
  const nextWords = next.split(" ");
  const maxOverlap = Math.min(12, prevWords.length, nextWords.length);
  for (let size = maxOverlap; size >= 2; size -= 1) {
    const prevTail = prevWords.slice(-size).join(" ").toLowerCase();
    const nextHead = nextWords.slice(0, size).join(" ").toLowerCase();
    if (prevTail === nextHead) {
      return cleanTranscript([...prevWords, ...nextWords.slice(size)].join(" "));
    }
  }

  return cleanTranscript(`${prev} ${next}`);
}

function turnDisplayText(turn: TranscriptTurn): string {
  return [turn.finalTranscript, turn.liveTranscript].map((part) => normalize(part)).filter(Boolean).join(turn.finalTranscript && turn.liveTranscript ? " " : "");
}

function finalizeTurn(turn: TranscriptTurn): TranscriptTurn {
  const committedLive = cleanTranscript(turn.liveTranscript);
  const finalTranscript = committedLive
    ? appendCommittedTranscript(turn.finalTranscript, committedLive)
    : normalize(turn.finalTranscript);
  return {
    ...turn,
    finalTranscript,
    liveTranscript: "",
    isStreaming: false,
    isFinal: true,
    timestamp: new Date().toISOString(),
  };
}

function finalizeTurns(turns: TranscriptTurn[]): TranscriptTurn[] {
  return turns.map((turn) => finalizeTurn(turn));
}

function updateTranscriptTurns(
  turns: TranscriptTurn[],
  role: TranscriptRole,
  incoming: string,
  isFinal = false,
): TranscriptTurn[] {
  const normalizedIncoming = normalize(incoming);
  if (!normalizedIncoming) return turns;

  const next = [...turns];
  const timestamp = new Date().toISOString();
  const last = next[next.length - 1];

  if (!last || last.role !== role) {
    if (last && last.isStreaming) {
      next[next.length - 1] = finalizeTurn(last);
    }
    next.push({
      id: createMessageId(role),
      role,
      speaker: speakerLabel(role),
      finalTranscript: isFinal ? cleanTranscript(normalizedIncoming) : "",
      liveTranscript: isFinal ? "" : normalizedIncoming,
      isStreaming: !isFinal,
      isFinal,
      timestamp,
    });
    return next;
  }

  if (isFinal) {
    const cleanedIncoming = cleanTranscript(normalizedIncoming);
    next[next.length - 1] = {
      ...last,
      speaker: speakerLabel(role),
      finalTranscript: appendCommittedTranscript(last.finalTranscript, cleanedIncoming || last.liveTranscript),
      liveTranscript: "",
      isStreaming: false,
      isFinal: true,
      timestamp,
    };
    return next;
  }

  next[next.length - 1] = {
    ...last,
    speaker: speakerLabel(role),
    liveTranscript: normalizedIncoming,
    isStreaming: true,
    isFinal: false,
    timestamp,
  };
  return next;
}

function debugTranscriptUpdate(details: {
  role: TranscriptRole;
  isFinal: boolean;
  previous: string;
  incoming: string;
  merged: string;
  reason: string;
}) {
  debugVoice("transcript_update", {
    role: details.role,
    isFinal: details.isFinal,
    previousLength: details.previous.length,
    incomingLength: details.incoming.length,
    mergedLength: details.merged.length,
    reason: details.reason,
    previousPreview: details.previous.slice(0, 120),
    incomingPreview: details.incoming.slice(0, 120),
    mergedPreview: details.merged.slice(0, 160),
  });
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

  const [transcriptTurns, setTranscriptTurns] = useState<TranscriptTurn[]>([]);
  const [pipelineStatus, setPipelineStatus] = useState<"idle" | "refining" | "fetching" | "done" | "error">("idle");
  const [pipelineError, setPipelineError] = useState("");
  const [intelligence, setIntelligence] = useState<RecruiterIntelligenceSession | null>(null);
  const [intelligenceLoading, setIntelligenceLoading] = useState(false);

  // Refs — never cause re-renders, safe to read inside Vapi callbacks
  const vapiRef = useRef<Vapi | null>(null);
  const turnsRef = useRef<TranscriptTurn[]>([]);  // committed + live transcript turns
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
  }, [transcriptTurns]);
  const selectionMood = job.vettingMode || "volume";

  const activeStreamingMessage = transcriptTurns.find((msg) => msg.isStreaming);
  const activeSpeaker = activeStreamingMessage?.role || null;
  const activeSpeakerLabel =
    activeSpeaker === "assistant" ? "Adam is speaking" : activeSpeaker === "user" ? "Recruiter is speaking" : "Waiting for speech";

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
    const incoming = normalize(text);
    if (!incoming) return;

    setTranscriptTurns((prev) => {
      const next = updateTranscriptTurns(prev, role, incoming, isFinal);
      const last = next[next.length - 1];
      debugTranscriptUpdate({
        role,
        isFinal,
        previous: "",
        incoming,
        merged: last ? turnDisplayText(last) : "",
        reason: isFinal ? "finalized" : "streaming_update",
      });
      return next;
    });

    turnsRef.current = updateTranscriptTurns(turnsRef.current, role, incoming, isFinal);
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
    debugVoice("transcript_event", {
      role: event.role,
      isFinal: event.isFinal,
      textLength: event.text.length,
      preview: event.text.slice(0, 120),
    });
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
  const runPipeline = useCallback(async (turns: TranscriptTurn[]) => {
    if (firedRef.current) return;
    firedRef.current = true;

    const finalizedTurns = finalizeTurns(turns);
    const fullTranscript = buildFullTranscript(finalizedTurns);
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

    if (user && jobId) {
      const intelligenceResult = await updateRecruiterIntelligence(user.id, jobId, {
        jobId,
        transcript: fullTranscript,
        voiceSummary: fullTranscript,
        entities: {},
      });
      if (intelligenceResult.success && intelligenceResult.data) {
        setIntelligence(intelligenceResult.data);
      }
    }

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
      const finalizedTurns = finalizeTurns(turnsRef.current);
      turnsRef.current = finalizedTurns;
      setTranscriptTurns(finalizedTurns);
      // Auto-trigger pipeline with everything captured so far
      void runPipeline(finalizedTurns);
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
    setTranscriptTurns([]);
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
      ? `You're hiring a ${jobTitle} at ${companyName}${location ? ` in ${location}` : ""}. Let's focus on this : ${firstQuestion}. `
      : `Let's refine your job requirements. ${firstQuestion}. I'll summarize what I captured, ask if you'd like to add anything, and then close the call once you confirm we're good.`;
    const closingInstructions = [
      "When the recruiter confirms the intake is complete, acknowledge it briefly, say thanks for the input, and end the call.",
      "If the recruiter wants to add anything else, capture it first before closing.",
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

  const pipelineLabel: Record<typeof pipelineStatus, string> = {
    idle: "",
    refining: "Analysing conversation and updating job profile...",
    fetching: "Running candidate search with updated requirements...",
    done: "Done — loading your candidates.",
    error: pipelineError,
  };

  return (
    <div className="space-y-6">
      <div className="rounded-[28px] border border-gray-200 bg-white p-6 shadow-sm md:p-8">
        <div className="mb-5 flex flex-col gap-3">
          <p className="text-2xl font-semibold text-[#111827]">Live voice transcript</p>
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] text-[#166534]">
            <span className="h-2 w-2 rounded-full bg-[#1F6F4A]" />
            <span>{activeSpeakerLabel}</span>
          </div>
        </div>

        <div
          ref={chatScrollRef}
          className="max-h-[70vh] space-y-4 overflow-y-auto rounded-[28px] border border-[#E7E0D4] bg-[linear-gradient(180deg,#FFFDF9_0%,#F7F2E8_100%)] p-4 shadow-[0_8px_24px_rgba(0,0,0,0.04)] md:p-6"
        >
          {transcriptTurns.length > 0 ? (
            transcriptTurns.map((message) => (
              <ChatBubble
                key={message.id}
                message={{
                  id: message.id,
                  role: message.role,
                  speaker: message.speaker,
                  content: turnDisplayText(message),
                  isStreaming: message.isStreaming,
                  isFinal: message.isFinal,
                  timestamp: message.timestamp,
                }}
                isInterim={message.isStreaming}
              />
            ))
          ) : (
            <div className="rounded-[24px] border border-dashed border-[#D8CCBA] bg-white/70 px-5 py-8 text-center text-sm text-[#6B7280]">
              Start the call and the final Adam and recruiter transcript will appear here.
            </div>
          )}
        </div>

        <div className="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-medium text-[#111827]">Current status</p>
            <p className="text-sm text-[#6B7280]">{callStatusLabel[callStatus] || "Ready to capture voice."}</p>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
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
          </div>
        </div>

        {(pipelineStatus !== "idle" || isErrorState) && (
          <div className={`mt-4 rounded-3xl p-4 text-sm ${pipelineStatus === "error" ? "bg-red-50 text-red-700" : "bg-[#F3F4F6] text-[#374151]"}`}>
            {pipelineStatus !== "error" && pipelineStatus !== "done" ? (
              <div className="flex items-center gap-2">
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-gray-300 border-t-[#1F6F4A]" />
                <span>{pipelineLabel[pipelineStatus]}</span>
              </div>
            ) : (
              <span>{pipelineLabel[pipelineStatus]}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
