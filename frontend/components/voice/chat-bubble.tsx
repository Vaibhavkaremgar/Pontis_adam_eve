"use client";

import Image from "next/image";

export type ChatMessage = {
  id: string;
  role: "assistant" | "user";
  speaker: "Adam" | "Recruiter";
  content: string;
  isStreaming: boolean;
  isFinal: boolean;
  timestamp: string;
};

export function ChatBubble({
  message,
  isInterim = false,
}: {
  message: ChatMessage;
  isInterim?: boolean;
}) {
  const bubbleClasses =
    message.role === "assistant"
      ? `ml-0 mr-auto flex-1 min-w-0 rounded-[20px] border border-[#E7E0D4] bg-gradient-to-b from-white to-[#FCFAF6] px-4 py-3 text-[13px] leading-6 text-[#111827] shadow-[0_8px_22px_rgba(0,0,0,0.05)] ${
          isInterim ? "opacity-75" : ""
        }`
      : `ml-auto mr-0 flex-1 min-w-0 rounded-[20px] border border-[#CFE7D8] bg-[#0F6B3A] px-4 py-3 text-sm leading-7 text-white shadow-[0_10px_22px_rgba(15,107,58,0.16)] ${
          isInterim ? "opacity-75" : ""
        }`;

  const shellClasses = message.role === "assistant" ? "items-start" : "items-end";
  const timeLabel = new Date(message.timestamp).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });

  if (message.role === "assistant") {
    return (
      <div className={`flex ${shellClasses} gap-2.5 animate-[fadeIn_220ms_ease-out] ${isInterim ? "opacity-75" : ""}`}>
        <Image
          src="/images/adam.png"
          alt="Adam"
          width={28}
          height={28}
          className="mt-0.5 h-7 w-7 rounded-full object-cover ring-2 ring-white/90"
        />
        <div className={bubbleClasses}>
          <div className="mb-2 flex items-center justify-between gap-3">
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-[#6B7280]">Adam</p>
            <p className="text-[10px] font-medium text-[#9CA3AF]">{timeLabel}</p>
          </div>
          <div className="whitespace-pre-wrap break-words">
            {message.content}
            {message.isStreaming && <span className="ml-1 inline-block align-middle text-[#0F6B3A]">|</span>}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`flex ${shellClasses} gap-3 animate-[fadeIn_220ms_ease-out] ${isInterim ? "opacity-75" : ""}`}>
      <div className={bubbleClasses}>
        <div className="mb-2 flex items-center justify-between gap-3">
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-white/70">Recruiter</p>
          <p className="text-[10px] font-medium text-white/55">{timeLabel}</p>
        </div>
        <div className="whitespace-pre-wrap break-words">
          {message.content}
          {message.isStreaming && <span className="ml-1 inline-block animate-pulse align-middle">|</span>}
        </div>
      </div>
      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#0F6B3A] text-sm font-semibold text-white shadow-[0_6px_18px_rgba(15,107,58,0.22)]">
        R
      </div>
    </div>
  );
}
