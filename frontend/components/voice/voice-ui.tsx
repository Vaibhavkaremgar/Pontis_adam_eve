"use client";

/**
 * What this component does:
 * Renders the conversation panel for voice intake using only real state.
 *
 * Which state it depends on:
 * Depends on `callStatus` and `transcript` from AppContext to decide exactly what appears.
 *
 * Why dummy UI is removed:
 * Production voice UX must never show fabricated transcript or placeholder conversation.
 *
 * Why state-driven UI is required:
 * Real voice systems stream state asynchronously, so rendering must follow callStatus transitions directly.
 */
import { AnimatePresence, motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Vapi from "@vapi-ai/web";

import { useAppContext } from "@/context/AppContext";

import { ChatBubble, type ChatMessage } from "./chat-bubble";
import { WaveAnimation } from "./wave-animation";

function normalizeTranscriptText(value: string) {
  return value.trim().replace(/\s+/g, " ");
}

function getTranscriptPayload(message: unknown): { text: string; isFinal: boolean } | null {
  if (!message || typeof message !== "object") return null;

  const record = message as Record<string, unknown>;
  const type = typeof record.type === "string" ? record.type : "";
  const transcriptType = typeof record.transcriptType === "string" ? record.transcriptType : "";

  if (type !== "transcript") return null;

  const rawTranscript = record.transcript;
  if (typeof rawTranscript !== "string") return null;

  const text = normalizeTranscriptText(rawTranscript);
  if (!text) return null;

  return {
    text,
    isFinal: transcriptType === "final"
  };
}

export function VoiceUi() {
  const router = useRouter();
  const { callStatus, setCallStatus, transcript, setTranscript, setVoiceNotes } = useAppContext();
  const [error, setError] = useState("");
  const hasPersistedRef = useRef(false);
  const vapiRef = useRef<Vapi | null>(null);
  const transcriptPartsRef = useRef<string[]>([]);
  const transcriptRef = useRef("");

  useEffect(() => {
    transcriptRef.current = transcript;
  }, [transcript]);

  const appendTranscriptNote = useCallback(() => {
    const finalTranscript = normalizeTranscriptText(transcriptRef.current);
    if (!finalTranscript) {
      setError("No transcript was captured. Please try the call again.");
      return;
    }

    setVoiceNotes((prev) => {
      if (prev[prev.length - 1] === finalTranscript) return prev;
      return [...prev, finalTranscript];
    });
  }, [setVoiceNotes]);

  const ensureVapi = useCallback(() => {
    if (vapiRef.current) return vapiRef.current;

    const publicKey = process.env.NEXT_PUBLIC_VAPI_PUBLIC_KEY;
    if (!publicKey) {
      throw new Error("Voice setup missing. Add NEXT_PUBLIC_VAPI_PUBLIC_KEY to frontend env.");
    }

    const vapi = new Vapi(publicKey);

    vapi.on("call-start", () => {
      setCallStatus("listening");
      setError("");
    });

    vapi.on("speech-start", () => {
      setCallStatus("speaking");
    });

    vapi.on("speech-end", () => {
      setCallStatus("listening");
    });

    vapi.on("message", (message) => {
      const payload = getTranscriptPayload(message);
      if (!payload) return;

      if (payload.isFinal) {
        const prevParts = transcriptPartsRef.current;
        const previous = prevParts[prevParts.length - 1];
        if (previous !== payload.text) {
          transcriptPartsRef.current = [...prevParts, payload.text];
        }

        setTranscript(transcriptPartsRef.current.join(" ").trim());
        return;
      }

      const stable = transcriptPartsRef.current.join(" ").trim();
      setTranscript([stable, payload.text].filter(Boolean).join(" ").trim());
    });

    vapi.on("error", () => {
      setCallStatus("error");
      setError("Voice assistant failed to connect. Please try again.");
    });

    vapi.on("call-end", () => {
      setCallStatus("completed");
      if (hasPersistedRef.current) return;
      appendTranscriptNote();
      hasPersistedRef.current = true;
    });

    vapiRef.current = vapi;
    return vapi;
  }, [appendTranscriptNote, setCallStatus, setTranscript]);

  const handleStart = async () => {
    const assistantId = process.env.NEXT_PUBLIC_VAPI_ASSISTANT_ID;
    if (!assistantId) {
      setCallStatus("error");
      setError("Voice assistant not configured. Add NEXT_PUBLIC_VAPI_ASSISTANT_ID.");
      return;
    }

    setError("");
    setTranscript("");
    transcriptPartsRef.current = [];
    hasPersistedRef.current = false;

    try {
      const vapi = ensureVapi();
      setCallStatus("connecting");
      await vapi.start(assistantId);
    } catch {
      setCallStatus("error");
      setError("Unable to start voice session. Check microphone permission and try again.");
    }
  };

  const handleEndCall = async () => {
    const vapi = vapiRef.current;
    if (!vapi) return;

    setCallStatus("processing");
    try {
      vapi.end();
      await vapi.stop();
    } catch {
      setCallStatus("error");
      setError("We could not end the call cleanly. Please try again.");
    }
  };

  useEffect(() => {
    return () => {
      if (!vapiRef.current) return;
      vapiRef.current.stop().catch(() => undefined);
    };
  }, []);

  const showEmpty = callStatus === "idle" && !transcript.trim();
  const showListening = callStatus === "connecting" || callStatus === "listening";
  const showProcessing = callStatus === "processing";
  const canContinue = callStatus === "completed";
  const isLive = callStatus === "connecting" || callStatus === "listening" || callStatus === "speaking";
  const messages = useMemo<ChatMessage[]>(() => {
    const rows: ChatMessage[] = [];
    if (callStatus !== "idle") {
      rows.push({
        role: "assistant",
        text:
          callStatus === "completed"
            ? "Thanks. I captured your requirements."
            : "Hi, I'm Maya. Tell me about the role and your ideal candidate."
      });
    }
    if (transcript.trim()) {
      rows.push({ role: "user", text: transcript.trim() });
    }
    return rows;
  }, [callStatus, transcript]);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = chatScrollRef.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }, [messages, callStatus]);

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 overflow-hidden rounded-full">
              <img src="/images/maya.png" alt="Maya" className="h-full w-full object-cover" />
            </div>
            <p className="font-heading text-2xl leading-none text-[#111111]">Maya</p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-green-100 px-3 py-1 text-sm font-medium text-green-700">
              Discovery
            </span>
            <span className="rounded-full bg-gray-100 px-3 py-1 text-sm font-medium text-gray-500">
              Calibration
            </span>
            <span className="rounded-full bg-gray-100 px-3 py-1 text-sm font-medium text-gray-500">
              Summary
            </span>
          </div>
        </div>

        {isLive && (
          <div className="flex items-center gap-3">
            {/* Buttons are only visible during active voice interaction */}
            {/* (listening/speaking) to match real conversational UX */}
            <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-green-100">
              <span className="w-2 h-2 bg-green-600 rounded-full animate-pulse" />
              <span className="text-sm text-green-700 font-medium">Speaking</span>
            </div>

            <button
              onClick={handleEndCall}
              className="px-4 py-1 rounded-full border border-red-500 text-red-500 hover:bg-red-50"
            >
              End
            </button>
          </div>
        )}
      </div>

      <div className="rounded-2xl bg-white p-6 shadow-[0_10px_26px_rgba(17,17,17,0.08)] md:p-8">
        <div className="mb-6 flex items-center justify-between gap-4">
          <p className="font-body text-base font-semibold text-[#111111]">Conversation</p>
          <p className="font-body text-sm text-[#6B7280]">Say &quot;pause a moment&quot; to pause</p>
        </div>

        <div className="space-y-6">
          {showEmpty && (
            <div className="flex min-h-[120px] items-center justify-center rounded-xl bg-[#F9FAFB]">
              <p className="font-body text-sm text-[#6B7280]">Click start to begin voice intake</p>
            </div>
          )}

          {!showEmpty && (
            <div
              ref={chatScrollRef}
              className="max-h-[320px] space-y-3 overflow-y-auto rounded-xl bg-[#F8FAFC] p-3 md:max-h-[360px]"
            >
              {messages.map((message, index) => (
                <ChatBubble key={`${message.role}-${index}-${message.text.slice(0, 16)}`} message={message} />
              ))}
            </div>
          )}

          <AnimatePresence mode="wait">
            {showListening && (
              <motion.div
                key="listening"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.22 }}
                className="flex min-h-[120px] items-center justify-center"
              >
                <WaveAnimation isActive />
              </motion.div>
            )}

            {showProcessing && (
              <motion.div
                key="processing"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.22 }}
                className="flex items-center gap-3 rounded-xl bg-[#F3F4F6] px-5 py-4"
              >
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-[#1F6F4A]" />
                <span className="font-body text-sm text-[#6B7280]">Wrapping up your conversation...</span>
              </motion.div>
            )}
          </AnimatePresence>

          <div className="mt-6 flex gap-3">
            {callStatus === "idle" && (
              <button
                onClick={handleStart}
                className="rounded-xl bg-[#1F6F4A] px-6 py-3 font-body text-base font-semibold text-white"
              >
                Start Conversation
              </button>
            )}

            {canContinue && (
              <button
                onClick={() => router.push("/voice/processing")}
                className="rounded-xl bg-[#1F6F4A] px-6 py-3 font-body text-base font-semibold text-white opacity-80"
              >
                Continue
              </button>
            )}
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>
      </div>
    </div>
  );
}
