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
import Image from "next/image";
import { AnimatePresence, motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { useAppContext } from "@/context/AppContext";

import { ChatBubble } from "./chat-bubble";
import { WaveAnimation } from "./wave-animation";

export function VoiceUi() {
  const router = useRouter();
  const { callStatus, setCallStatus, transcript, setTranscript, voiceNotes, setVoiceNotes } = useAppContext();
  const hasPersistedRef = useRef(false);

  const handleStart = () => {
    setTranscript("");
    hasPersistedRef.current = false;
    setCallStatus("listening");
  };

  const handleEndCall = () => {
    setCallStatus("completed");
  };

  useEffect(() => {
    if (callStatus !== "completed" || hasPersistedRef.current) return;
    if (!transcript.trim()) return;

    setVoiceNotes([...voiceNotes, transcript.trim()]);
    hasPersistedRef.current = true;
  }, [callStatus, setVoiceNotes, transcript, voiceNotes]);

  const showEmpty = callStatus === "idle" && !transcript.trim();
  const showListening = callStatus === "listening";
  const showSpeakingBubble = callStatus === "speaking" && transcript.trim().length > 0;
  const showProcessing = callStatus === "processing";
  const showCompletedBubble = callStatus === "completed" && transcript.trim().length > 0;
  const canContinue = callStatus === "completed";
  const isLive = callStatus === "listening" || callStatus === "speaking";

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 overflow-hidden rounded-full">
              <Image
                src="/images/Maya.jpg.jpeg"
                alt="Maya"
                width={40}
                height={40}
                className="h-full w-full object-cover"
              />
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
          <p className="font-body text-sm text-[#6B7280]">Say "pause a moment" to pause</p>
        </div>

        <div className="space-y-6">
          {showEmpty && (
            <div className="flex min-h-[120px] items-center justify-center rounded-xl bg-[#F9FAFB]">
              <p className="font-body text-sm text-[#6B7280]">Click start to begin voice intake</p>
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

            {showSpeakingBubble && (
              <motion.div
                key="speaking"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.22 }}
              >
                <ChatBubble text={transcript} />
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
                <span className="font-body text-sm text-[#6B7280]">Understanding your requirements...</span>
              </motion.div>
            )}

            {showCompletedBubble && (
              <motion.div
                key="completed"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.22 }}
              >
                <ChatBubble text={transcript} />
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
        </div>
      </div>
    </div>
  );
}
