"use client";

/**
 * What this component does:
 * Renders a clean AI conversation bubble without decorative placeholders.
 *
 * Which state it depends on:
 * Depends on transcript text passed from callStatus-driven voice state.
 */

export function ChatBubble({ text }: { text: string }) {
  return (
    <div className="max-w-[600px] rounded-xl bg-gray-100 px-5 py-4 font-body text-base leading-relaxed text-[#111111]">
      {text}
    </div>
  );
}
