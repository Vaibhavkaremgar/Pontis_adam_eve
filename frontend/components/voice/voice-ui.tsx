"use client";

/**
 * What this component does:
 * Full production voice intake UI.
 * - Starts Vapi directly with job context injected as variableValues + dynamic firstMessage
 * - Captures BOTH assistant and user turns as structured VoiceTurn[]
 * - On call-end: auto-triggers the appropriate completion path for the active mode
 * - Dashboard mode refines the job and navigates to /review
 * - Slack mode completes orchestration and stays out of dashboard routing
 * - Shows retry on failure
 */
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import Vapi from "@vapi-ai/web";
import Image from "next/image";
import { Mic } from "lucide-react";

import { useAppContext } from "@/context/AppContext";
import { getRecruiterIntelligence, updateRecruiterIntelligence } from "@/lib/api/recruiter-intelligence";
import { refineWithVoice } from "@/lib/api/voice";
import type { RecruiterIntelligenceSession } from "@/lib/api/recruiter-intelligence";
import { completeOrchestrationVoice } from "@/lib/api/orchestration";
import { Button } from "@/components/ui/button";

import { ChatBubble } from "./chat-bubble";
import { WaveAnimation } from "./wave-animation";
import {
  getConfiguredPublicAppUrl,
  getCurrentOrigin,
  getEffectiveVapiOrigin,
  getVapiRuntimeSnapshot,
  suggestVapiOriginHint,
} from "@/lib/vapi-runtime";

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

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
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

function stripTranscriptNoise(text: string) {
  return normalize(text)
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s.+-]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
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

const EXPECTATION_STEPS = [
  {
    id: 1,
    title: "Discovery",
    duration: "~5-8 min",
    details: [
      "Why this role exists now",
      "What they'll own end-to-end",
      "What success looks like at 1 year",
      "Ideal traits and background",
      "Red flags to avoid",
    ],
  },
  {
    id: 2,
    title: "Calibration",
    duration: "~3-5 min",
    details:
      "We’ll show 3-4 candidate pairs and ask which you prefer. This reveals your true priorities, like startup vs enterprise or top school vs top company.",
  },
  {
    id: 3,
    title: "Summary",
    duration: "~1 min",
    details: "We’ll confirm what we learned before generating your sourcing report.",
  },
] as const;

type IntakeCompletionSignal = {
  ready: boolean;
  coverage: number;
  requiredTopics: string[];
  satisfiedTopics: string[];
  latestNovelty: number;
  recruiterFinalTurns: number;
  assistantFinalTurns: number;
  reason: string;
};

function buildRequiredIntakeTopics(job: Record<string, unknown> | null | undefined) {
  const topics: Array<{ key: string; patterns: RegExp[]; minMatches?: number }> = [];
  const jobRecord = (job || {}) as Record<string, any>;
  const title = normalize(String(jobRecord.title || ""));
  const location = normalize(String(jobRecord.location || ""));
  const compensation = normalize(String(jobRecord.compensation || ""));
  const experienceRequired = normalize(String(jobRecord.experienceRequired || jobRecord.experience_required || ""));
  const workAuthorization = normalize(String(jobRecord.workAuthorization || jobRecord.work_authorization || ""));
  const remotePolicy = normalize(String(jobRecord.remotePolicy || jobRecord.remote_policy || ""));
  const skillsRequired = Array.isArray(jobRecord.skillsRequired)
    ? jobRecord.skillsRequired.filter(Boolean)
    : Array.isArray(jobRecord.skills_required)
      ? jobRecord.skills_required.filter(Boolean)
      : [];
  const titleTokens = title
    .split(/[^a-z0-9]+/i)
    .map((token) => token.trim())
    .filter((token) => token.length >= 3)
    .slice(0, 4);

  if (titleTokens.length > 0) {
    topics.push({
      key: "role",
      patterns: [
        ...titleTokens.map((token) => new RegExp(`\\b${escapeRegExp(token)}\\b`, "i")),
        /\b(role|title|position|hiring for|looking for|need a|need an|need the)\b/i,
      ],
    });
  }

  if (location) {
    const locationTokens = location.split(/[^a-z0-9]+/i).map((token) => token.trim()).filter((token) => token.length >= 3).slice(0, 4);
    topics.push({
      key: "location",
      patterns: [
        ...locationTokens.map((token) => new RegExp(`\\b${escapeRegExp(token)}\\b`, "i")),
        /\b(remote|hybrid|onsite|on-site|location|based in|relocation)\b/i,
      ],
    });
  }

  if (compensation) {
    topics.push({
      key: "compensation",
      patterns: [
        /\b(salary|compensation|ctc|pay|package|budget|rate|lpa|stipend)\b/i,
        /\b\d+(?:\.\d+)?\s*(?:k|lpa|lakhs?|crores?|usd|inr|eur|gbp)\b/i,
      ],
    });
  }

  if (experienceRequired) {
    topics.push({
      key: "experience",
      patterns: [
        /\b\d+(?:\.\d+)?\+?\s*(?:years?|yrs?)\b/i,
        /\b(junior|mid|senior|lead|staff|principal|experienced)\b/i,
      ],
    });
  }

  if (workAuthorization) {
    topics.push({
      key: "authorization",
      patterns: [
        /\b(work authorization|work authorisation|visa|sponsorship|eligible|citizen|permit)\b/i,
      ],
    });
  }

  if (remotePolicy) {
    topics.push({
      key: "remotePolicy",
      patterns: [
        /\b(remote|hybrid|onsite|on-site|office|in-person)\b/i,
      ],
    });
  }

  if (skillsRequired.length > 0) {
    topics.push({
      key: "skills",
      minMatches: Math.min(2, skillsRequired.length),
      patterns: [
        ...skillsRequired.slice(0, 8).map((skill) => new RegExp(`\\b${escapeRegExp(String(skill).trim())}\\b`, "i")),
      ],
    });
  }

  return topics;
}

function computeNoveltyRatio(latestText: string, priorText: string) {
  const latestTokens = stripTranscriptNoise(latestText).split(" ").filter(Boolean);
  const priorTokens = new Set(stripTranscriptNoise(priorText).split(" ").filter(Boolean));
  if (latestTokens.length === 0) return 0;
  const uniqueLatest = Array.from(new Set(latestTokens));
  const novelCount = uniqueLatest.filter((token) => !priorTokens.has(token)).length;
  return novelCount / uniqueLatest.length;
}

function evaluateIntakeCompletion(turns: TranscriptTurn[], job: Record<string, unknown> | null | undefined): IntakeCompletionSignal {
  const finalizedTurns = turns.filter((turn) => turn.isFinal);
  const recruiterTurns = finalizedTurns.filter((turn) => turn.role === "user");
  const assistantTurns = finalizedTurns.filter((turn) => turn.role === "assistant");
  const hasStreamingTurn = turns.some((turn) => turn.isStreaming);
  const requiredTopics = buildRequiredIntakeTopics(job);
  const transcriptByTurn = finalizedTurns.map((turn) => turnDisplayText(turn)).join("\n");
  const satisfiedTopics = requiredTopics.filter((topic) => {
    const matches = topic.patterns.filter((pattern) => pattern.test(transcriptByTurn)).length;
    return matches >= (topic.minMatches || 1);
  }).map((topic) => topic.key);
  const coverage = requiredTopics.length > 0 ? satisfiedTopics.length / requiredTopics.length : 0;
  const latestRecruiterTurn = recruiterTurns[recruiterTurns.length - 1];
  const priorRecruiterText = recruiterTurns.slice(0, -1).map((turn) => turnDisplayText(turn)).join("\n");
  const latestRecruiterText = latestRecruiterTurn ? turnDisplayText(latestRecruiterTurn) : "";
  const latestNovelty = latestRecruiterText ? computeNoveltyRatio(latestRecruiterText, `${priorRecruiterText}\n${assistantTurns.map((turn) => turnDisplayText(turn)).join("\n")}`) : 0;
  const latestWordCount = stripTranscriptNoise(latestRecruiterText).split(" ").filter(Boolean).length;
  const sufficientHistory = recruiterTurns.length >= 3 && assistantTurns.length >= 2;
  const highCoverage = requiredTopics.length > 0 ? coverage >= 0.8 : finalizedTurns.length >= 8;
  const lowNovelty = latestNovelty <= 0.25;
  const conciseWrapUp = latestWordCount > 0 && latestWordCount <= 18;
  const ready = Boolean(latestRecruiterTurn) && !hasStreamingTurn && sufficientHistory && highCoverage && (lowNovelty || conciseWrapUp || coverage >= 0.9);

  let reason = "waiting_for_more_context";
  if (ready) {
    reason = coverage >= 0.9 ? "coverage_complete" : lowNovelty ? "low_novelty_after_complete_coverage" : "concise_confirmation_after_complete_coverage";
  } else if (!latestRecruiterTurn) {
    reason = "waiting_for_recruiter_input";
  } else if (!sufficientHistory) {
    reason = "insufficient_conversation_history";
  } else if (!highCoverage) {
    reason = "intake_topics_still_open";
  } else if (!lowNovelty && !conciseWrapUp) {
    reason = "latest_turn_added_new_information";
  }

  return {
    ready,
    coverage,
    requiredTopics: requiredTopics.map((topic) => topic.key),
    satisfiedTopics,
    latestNovelty,
    recruiterFinalTurns: recruiterTurns.length,
    assistantFinalTurns: assistantTurns.length,
    reason,
  };
}

function assistantAskedForFinalConfirmation(turns: TranscriptTurn[]): boolean {
  const assistantText = turns
    .filter((turn) => turn.role === "assistant" && turn.isFinal)
    .map((turn) => turnDisplayText(turn))
    .slice(-2)
    .join(" ")
    .toLowerCase();

  return /anything else|anything you(?:'|)d like to add|anything more to add|proceed with this info|is there anything else|final summary/.test(assistantText);
}

function isWrapUpConfirmation(text: string): boolean {
  const normalized = stripTranscriptNoise(text);
  if (!normalized) return false;
  return /^(no|nope|nah|that's all|thats all|nothing else|nothing more|proceed|proceed with this info|sounds good|looks good|go ahead|all set|good to go|that's fine|thats fine|continue)$/i.test(
    normalized,
  );
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
      publicAppUrl?: string;
      hasPublicKey?: boolean;
      hasAssistantId?: boolean;
      hasPublicAppUrl?: boolean;
    };
    error?: string;
  };

  const publicKey = payload.data?.publicKey?.trim() || "";
  const assistantId = payload.data?.assistantId?.trim() || "";
  const publicAppUrl = payload.data?.publicAppUrl?.trim() || "";
  return { publicKey, assistantId, publicAppUrl };
}

// ─── component ────────────────────────────────────────────────────────────────

type VoiceUiProps = {
  completionMode?: "dashboard" | "slack";
  slackToken?: string;
};

export function VoiceUi({ completionMode = "dashboard", slackToken = "" }: VoiceUiProps) {
  const router = useRouter();
  const { callStatus, setCallStatus, setVoiceNotes, setIsRefined, jobId, job, company, user, isSessionReady } = useAppContext();
  const isSlackCompletionMode = completionMode === "slack" && Boolean(slackToken);

  const [transcriptTurns, setTranscriptTurns] = useState<TranscriptTurn[]>([]);
  const [pipelineStatus, setPipelineStatus] = useState<"idle" | "refining" | "done" | "error">("idle");
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
    const completion = evaluateIntakeCompletion(turnsRef.current, job as Record<string, unknown> | null | undefined);
    debugVoice("intake_completion_check", {
      ready: completion.ready,
      reason: completion.reason,
      coverage: completion.coverage,
      requiredTopics: completion.requiredTopics,
      satisfiedTopics: completion.satisfiedTopics,
      latestNovelty: completion.latestNovelty,
      recruiterFinalTurns: completion.recruiterFinalTurns,
      assistantFinalTurns: completion.assistantFinalTurns,
    });

    const wrapUpConfirmation = event.role === "user" && event.isFinal && assistantAskedForFinalConfirmation(turnsRef.current) && isWrapUpConfirmation(event.text);

    if (completion.ready || wrapUpConfirmation) {
      debugVoice("intake_wrapup_triggered", {
        completionReady: completion.ready,
        wrapUpConfirmation,
        reason: completion.reason,
      });
      if (autoEndTimerRef.current !== null) {
        window.clearTimeout(autoEndTimerRef.current);
      }
      autoEndTimerRef.current = window.setTimeout(() => {
        if (terminalStateRef.current === "live") {
          void requestCallStop();
        }
      }, 1200);
      return;
    }

    if (autoEndTimerRef.current !== null) {
      window.clearTimeout(autoEndTimerRef.current);
      autoEndTimerRef.current = null;
    }
  }, [job, requestCallStop, upsertStreamingMessage]);

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

    console.info("voice_completion_started", {
      jobId,
      completionMode,
      turnsCaptured: turns.length,
      transcriptLength: fullTranscript.length,
    });
    console.info("[voice] pipeline_start", {
      jobId,
      durationMs: callStartedAtRef.current ? endedAt - callStartedAtRef.current : null,
      turnsCaptured: turns.length,
    });

    if (isSlackCompletionMode) {
      console.info("complete_orchestration_voice_called", {
        jobId,
        tokenPreview: slackToken ? `${slackToken.slice(0, 6)}...${slackToken.slice(-4)}` : null,
        transcriptLength: fullTranscript.length,
      });
      console.info("dashboard_navigation_suppressed", {
        jobId,
        completionMode,
        reason: "slack_completion_mode",
      });

      setPipelineStatus("refining");
      const completionResult = await completeOrchestrationVoice(slackToken, {
        transcript: fullTranscript,
        voiceNotes: [fullTranscript],
      });

      if (!completionResult.success) {
        setPipelineStatus("error");
        setPipelineError(completionResult.error || "Could not complete Slack orchestration.");
        return;
      }

      console.info("slack_post_voice_continuation_triggered", {
        jobId,
        completed: Boolean(completionResult.data?.completed),
        nextQuestion: completionResult.data?.nextQuestion || "",
        hasFinalization: Boolean(completionResult.data?.finalization),
      });

      setVoiceNotes([fullTranscript]);
      setIsRefined(true);
      setPipelineStatus("done");
      terminalStateRef.current = "done";
      return;
    }

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

    setIsRefined(true);
    setPipelineStatus("done");
    terminalStateRef.current = "done";

    // Auto-navigate to review so the calibration gate can appear before sourcing.
    setTimeout(() => router.push("/review"), 1200);
  }, [completionMode, isSlackCompletionMode, jobId, router, setIsRefined, setVoiceNotes, slackToken, user]);

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
    const runtimeSnapshot = getVapiRuntimeSnapshot();
    debugVoice("runtime snapshot", runtimeSnapshot);
    console.log("Current origin:", getCurrentOrigin());
    console.log("VAPI_ASSISTANT_ID", process.env.NEXT_PUBLIC_VAPI_ASSISTANT_ID);
    console.log("NEXT_PUBLIC_PUBLIC_APP_URL", getConfiguredPublicAppUrl());
    let assistantId = process.env.NEXT_PUBLIC_VAPI_ASSISTANT_ID;
    let publicKey = process.env.NEXT_PUBLIC_VAPI_PUBLIC_KEY;
    let publicAppUrl = getConfiguredPublicAppUrl();
    debugVoice("env snapshot", {
      hasAssistantId: Boolean(assistantId),
      hasPublicKey: Boolean(publicKey),
      hasPublicAppUrl: Boolean(publicAppUrl),
      assistantIdPreview: assistantId ? `${assistantId.slice(0, 6)}...${assistantId.slice(-4)}` : null,
      publicKeyPreview: publicKey ? `${publicKey.slice(0, 6)}...${publicKey.slice(-4)}` : null,
      publicAppUrl,
    });

    if (!assistantId || !publicKey) {
      try {
        const runtimeConfig = await loadVapiConfig();
        assistantId = assistantId || runtimeConfig.assistantId;
        publicKey = publicKey || runtimeConfig.publicKey;
        publicAppUrl = publicAppUrl || runtimeConfig.publicAppUrl;
        debugVoice("runtime vapi config loaded", {
          hasAssistantId: Boolean(assistantId),
          hasPublicKey: Boolean(publicKey),
          hasPublicAppUrl: Boolean(publicAppUrl),
          assistantIdPreview: assistantId ? `${assistantId.slice(0, 6)}...${assistantId.slice(-4)}` : null,
          publicKeyPreview: publicKey ? `${publicKey.slice(0, 6)}...${publicKey.slice(-4)}` : null,
          publicAppUrl,
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
    const effectiveOrigin = publicAppUrl || getEffectiveVapiOrigin();
    const originHint = suggestVapiOriginHint(runtimeSnapshot);

    const interviewQuestions = intelligence?.interview?.recommended_questions || intelligence?.selection?.recommended_questions || [];
    const firstQuestion = intelligence?.interview?.current_question || interviewQuestions[0] || "What's the most important thing you're looking for in this candidate?";
    const questionList = interviewQuestions.length
      ? interviewQuestions.map((question, index) => `${index + 1}. ${question}`).join("\n")
      : firstQuestion;
    const firstMessage = companyName && jobTitle
      ? `You're hiring a ${jobTitle} at ${companyName}${location ? ` in ${location}` : ""}. Let's focus on this : ${firstQuestion}. `
      : `Let's refine your job requirements. ${firstQuestion}. I'll keep track of the requirements, summarize what I captured, and close the call only after the conversation looks complete.`;
    const closingInstructions = [
      "Keep collecting requirements until the conversation feels complete.",
      "When the answers appear complete, briefly summarize the captured requirements, ask if anything important is still missing, and then end the call once the recruiter says no, that's all, proceed with this info, or anything similar.",
      "If the recruiter adds new information, keep the call open and update the intake first.",
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
          currentOrigin: getCurrentOrigin(),
          publicAppUrl,
          effectiveOrigin,
          runtimeEnvironment: runtimeSnapshot.runtime,
          closingInstructions,
        },
        firstMessage,
      });
      debugVoice("vapi.start resolved");
    } catch (error) {
      terminalStateRef.current = "error";
      debugVoice("start_failed", {
        jobId,
        currentOrigin: getCurrentOrigin(),
        publicAppUrl,
        effectiveOrigin,
        runtimeEnvironment: runtimeSnapshot.runtime,
        originHint,
        error,
      });
      console.error("[vapi] start failed", {
        currentOrigin: getCurrentOrigin(),
        publicAppUrl,
        effectiveOrigin,
        assistantId,
        publicKeyPreview: publicKey.slice(0, 6) + "..." + publicKey.slice(-4),
        environment: runtimeSnapshot.runtime,
        originHint,
        error,
      });
      setCallStatus("error");
      setPipelineStatus("error");
      setPipelineError(`Unable to start voice session: ${toErrorMessage(error)}${originHint ? ` ${originHint}` : ""}`);
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
    done: "Done — loading calibration.",
    error: pipelineError,
  };

  const showIntro = (isIdle || isErrorState) && transcriptTurns.length === 0;

  if (showIntro) {
    return (
      <div className="mx-auto flex w-full max-w-[980px] justify-center px-4 py-5 sm:px-5 lg:px-6">
        <div className="w-full max-w-[780px] rounded-[30px] border border-[#E7E0D4] bg-white px-5 py-6 shadow-[0_10px_24px_rgba(0,0,0,0.05)] sm:px-6 sm:py-7 lg:px-8 lg:py-8">
          <div className="flex flex-col items-center text-center">
            <div className="flex h-[72px] w-[72px] items-center justify-center overflow-hidden rounded-full border border-[#D7E2F2] bg-[#F7FAFF] shadow-[0_4px_14px_rgba(146,183,248,0.14)]">
              <Image src="/images/ada.png" alt="Ada avatar" width={72} height={72} className="h-full w-full object-cover" priority />
            </div>
            <h1 className="mt-6 font-heading text-[28px] font-semibold tracking-[-0.03em] text-[#111827] sm:text-[30px]">
              Chat with Adam
            </h1>
            <p className="mt-3 max-w-xl font-body text-[15px] leading-6 text-[#8D857C]">
              Have a conversation with our AI about your hiring needs.
            </p>
          </div>

          <div className="mt-6 rounded-[24px] border border-[#E7E0D4] bg-[#FBF8F2] p-4 sm:p-5">
            <div className="flex items-center gap-3">
              <span className="text-lg">📋</span>
              <p className="font-heading text-[18px] font-semibold text-[#111827]">What to Expect</p>
            </div>

            <div className="mt-5 space-y-4">
              {EXPECTATION_STEPS.map((step) => (
                <div key={step.id} className="grid grid-cols-[auto_1fr_auto] gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#14532D] text-sm font-semibold text-white">
                    {step.id}
                  </div>
                  <div className="space-y-1">
                    <p className="font-body text-[15px] font-medium text-[#111827]">{step.title}</p>
                    {Array.isArray(step.details) ? (
                      <>
                        <p className="font-body text-[13px] text-[#8D857C]">We&apos;ll ask about:</p>
                        <ul className="space-y-1 pl-4 font-body text-[13px] leading-5 text-[#8D857C]">
                          {step.details.map((item) => (
                            <li key={`${step.id}-${item}`} className="list-disc">
                              {item}
                            </li>
                          ))}
                        </ul>
                      </>
                    ) : (
                      <p className="max-w-2xl font-body text-[13px] leading-5 text-[#8D857C]">{step.details}</p>
                    )}
                  </div>
                  <div className="pt-0.5 font-body text-[12px] text-[#8D857C]">{step.duration}</div>
                </div>
              ))}
            </div>

            <div className="mt-5 border-t border-[#E8DDCB] pt-4 text-center font-body text-[14px] text-[#8D857C]">
              Total: ~10-15 minutes
            </div>
          </div>

          <div className="mt-8 flex flex-col items-center gap-3">
            <Button
              size="lg"
              onClick={handleStart}
              className="min-w-[200px] rounded-full bg-[#14532D] px-7 py-3 text-[15px] font-semibold text-white shadow-[0_10px_20px_rgba(20,83,45,0.16)] hover:bg-[#0F3F23]"
            >
              <Mic className="mr-2 h-4 w-4" />
              Start Conversation
            </Button>

            <div className="max-w-xl rounded-2xl px-3 py-2 text-center">
              <p className="text-xs font-medium text-[#111827]">Current status</p>
              <p className="text-xs text-[#6B7280]">{callStatusLabel[callStatus] || "Ready to capture voice."}</p>
            </div>
          </div>

          {(pipelineStatus !== "idle" || isErrorState) && (
            <div className={`mt-4 rounded-2xl p-3 text-sm ${pipelineStatus === "error" ? "bg-red-50 text-red-700" : "bg-[#F3F4F6] text-[#374151]"}`}>
              {pipelineStatus !== "error" && pipelineStatus !== "done" ? (
                <div className="flex items-center gap-2">
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-gray-300 border-t-[#1F6F4A]" />
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

  return (
    <div className="space-y-5">
      <div className="rounded-[24px] border border-gray-200 bg-white p-5 shadow-sm md:p-6">
        <div className="mb-4 flex flex-col gap-2">
          <p className="font-heading text-[28px] font-semibold text-[#111827]">Live voice transcript</p>
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-[#166534]">
            <span className="h-2 w-2 rounded-full bg-[#1F6F4A]" />
            <span>{activeSpeakerLabel}</span>
          </div>
        </div>

        <div
          ref={chatScrollRef}
          className="max-h-[68vh] space-y-3 overflow-y-auto rounded-[24px] border border-[#E7E0D4] bg-[linear-gradient(180deg,#FFFDF9_0%,#F7F2E8_100%)] p-3 shadow-[0_8px_22px_rgba(0,0,0,0.04)] md:p-4"
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
            <div className="rounded-[20px] border border-dashed border-[#D8CCBA] bg-white/70 px-4 py-6 text-center text-sm text-[#6B7280]">
              Start the call and the final Adam and recruiter transcript will appear here.
            </div>
          )}
        </div>

        <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-medium text-[#111827]">Current status</p>
            <p className="text-xs text-[#6B7280]">{callStatusLabel[callStatus] || "Ready to capture voice."}</p>
          </div>

          <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center">
            {(isIdle || isErrorState) && (
              <button
                onClick={handleStart}
                className="rounded-xl bg-[#1F6F4A] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#184E3C]"
              >
                {isErrorState ? "Retry voice intake" : "Start voice intake"}
              </button>
            )}
            {isLive && (
              <button
                onClick={handleEndCall}
                className="rounded-xl border border-red-500 px-4 py-2.5 text-sm font-semibold text-red-600 hover:bg-red-50"
              >
                End call now
              </button>
            )}
          </div>
        </div>

        {(pipelineStatus !== "idle" || isErrorState) && (
          <div className={`mt-4 rounded-2xl p-3 text-sm ${pipelineStatus === "error" ? "bg-red-50 text-red-700" : "bg-[#F3F4F6] text-[#374151]"}`}>
            {pipelineStatus !== "error" && pipelineStatus !== "done" ? (
              <div className="flex items-center gap-2">
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-gray-300 border-t-[#1F6F4A]" />
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
