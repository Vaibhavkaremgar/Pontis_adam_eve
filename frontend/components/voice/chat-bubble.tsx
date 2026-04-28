"use client";

export type ChatMessage = {
  role: "assistant" | "user";
  text: string;
  isFinal?: boolean;
};

export function ChatBubble({ message }: { message: ChatMessage }) {
  if (message.role === "assistant") {
    return (
      <div className="flex items-start gap-2 animate-[fadeIn_220ms_ease-out]">
        <img src="/images/maya.png" alt="Maya" className="h-8 w-8 rounded-full object-cover" />
        <div className="max-w-[70%] rounded-xl bg-gray-200/90 px-4 py-2 text-sm text-[#111111] shadow-sm">{message.text}</div>
      </div>
    );
  }

  return (
    <div className="flex items-start justify-end gap-2 animate-[fadeIn_220ms_ease-out]">
      <div className="max-w-[70%] rounded-xl bg-green-500 px-4 py-2 text-sm text-white shadow-sm">{message.text}</div>
      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-gray-800 text-sm font-semibold text-white">R</div>
    </div>
  );
}
