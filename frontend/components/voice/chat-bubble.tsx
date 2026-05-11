"use client";

export type ChatMessage = {
  id: string;
  role: "assistant" | "user";
  speaker: "Adam" | "You";
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
      ? `ml-0 mr-auto max-w-[78%] rounded-[20px] border border-[#E7E0D4] bg-white px-5 py-4 text-sm leading-7 text-[#111827] shadow-[0_8px_20px_rgba(0,0,0,0.04)] ${
          isInterim ? "opacity-75" : ""
        }`
      : `ml-auto mr-0 max-w-[78%] rounded-[20px] border border-[#CFE7D8] bg-[#0F6B3A] px-5 py-4 text-sm leading-7 text-white shadow-[0_8px_20px_rgba(15,107,58,0.14)] ${
          isInterim ? "opacity-75" : ""
        }`;

  const shellClasses = message.role === "assistant" ? "items-start" : "items-end";

  if (message.role === "assistant") {
    return (
      <div className={`flex ${shellClasses} gap-3 animate-[fadeIn_220ms_ease-out] ${isInterim ? "opacity-75" : ""}`}>
        <img src="/images/adam.png" alt="Adam" className="mt-1 h-8 w-8 rounded-full object-cover" />
        <div className={bubbleClasses}>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-[#6B7280]">Adam</p>
          <div className="whitespace-pre-wrap break-words">{message.content}{message.isStreaming && <span className="ml-1 inline-block animate-pulse align-middle">▍</span>}</div>
        </div>
      </div>
    );
  }

  return (
    <div className={`flex ${shellClasses} gap-3 animate-[fadeIn_220ms_ease-out] ${isInterim ? "opacity-75" : ""}`}>
      <div className={bubbleClasses}>
        <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-white/70">You</p>
        <div className="whitespace-pre-wrap break-words">{message.content}{message.isStreaming && <span className="ml-1 inline-block animate-pulse align-middle">▍</span>}</div>
      </div>
      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#0F6B3A] text-sm font-semibold text-white">Y</div>
    </div>
  );
}
