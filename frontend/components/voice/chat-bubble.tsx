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
      ? `ml-0 mr-auto w-fit max-w-[82%] min-w-0 rounded-[18px] border border-[#E7E0D4] bg-gradient-to-b from-white to-[#FCFAF6] px-3.5 py-2.5 text-[12px] leading-5 text-[#111827] shadow-[0_8px_20px_rgba(0,0,0,0.05)] ${
          isInterim ? "opacity-75" : ""
        }`
      : `ml-auto mr-0 w-fit max-w-[82%] min-w-0 rounded-[18px] border border-[#CFE7D8] bg-[#0F6B3A] px-3.5 py-2.5 text-[13px] leading-6 text-white shadow-[0_10px_22px_rgba(15,107,58,0.16)] ${
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
          src="/images/ada.png"
          alt="Ada avatar"
          width={24}
          height={24}
          className="mt-0.5 h-6 w-6 rounded-full object-cover ring-2 ring-white/90"
        />
        <div className={bubbleClasses}>
          <div className="mb-2 flex items-center justify-between gap-3">
            <p className="text-[9px] font-semibold uppercase tracking-[0.18em] text-[#6B7280]">Ada</p>
            <p className="text-[9px] font-medium text-[#9CA3AF]">{timeLabel}</p>
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
    <div className={`flex ${shellClasses} gap-2.5 animate-[fadeIn_220ms_ease-out] ${isInterim ? "opacity-75" : ""}`}>
      <div className={bubbleClasses}>
        <div className="mb-2 flex items-center justify-between gap-3">
          <p className="text-[9px] font-semibold uppercase tracking-[0.18em] text-white/70">Recruiter</p>
          <p className="text-[9px] font-medium text-white/55">{timeLabel}</p>
        </div>
        <div className="whitespace-pre-wrap break-words">
          {message.content}
          {message.isStreaming && <span className="ml-1 inline-block animate-pulse align-middle">|</span>}
        </div>
      </div>
      <div className="flex h-7 w-7 items-center justify-center rounded-full bg-[#0F6B3A] text-[11px] font-semibold text-white shadow-[0_6px_18px_rgba(15,107,58,0.22)]">
        R
      </div>
    </div>
  );
}
