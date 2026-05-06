"use client";

export type ChatMessage = {
  role: "assistant" | "user";
  text: string;
  isFinal?: boolean;
};

export function ChatBubble({
  message,
  isInterim = false,
}: {
  message: ChatMessage;
  isInterim?: boolean;
}) {
  if (message.role === "assistant") {
    return (
      <div className={`flex items-start gap-2 animate-[fadeIn_220ms_ease-out] ${isInterim ? "opacity-70" : ""}`}>
        <img src="/images/maya.png" alt="Maya" className="h-8 w-8 rounded-full object-cover" />
        <div
          className={`max-w-[70%] rounded-xl px-4 py-2 text-sm shadow-sm ${
            isInterim
              ? "border border-dashed border-gray-300 bg-white/70 text-gray-600"
              : "bg-gray-200/90 text-[#111111]"
          }`}
        >
          {message.text}
        </div>
      </div>
    );
  }

  return (
    <div className={`flex items-start justify-end gap-2 animate-[fadeIn_220ms_ease-out] ${isInterim ? "opacity-70" : ""}`}>
      <div
        className={`max-w-[70%] rounded-xl px-4 py-2 text-sm shadow-sm ${
          isInterim ? "border border-dashed border-green-300 bg-green-50 text-green-800" : "bg-green-500 text-white"
        }`}
      >
        {message.text}
      </div>
      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-gray-800 text-sm font-semibold text-white">R</div>
    </div>
  );
}
