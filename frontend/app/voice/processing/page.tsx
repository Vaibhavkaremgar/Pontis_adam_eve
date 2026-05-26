import { Suspense } from "react";
import VoiceProcessingClient from "./voice-processing-client";

export const dynamic = "force-dynamic";

export default function VoiceProcessingPage() {
  return (
    <Suspense fallback={<div className="mx-auto w-full max-w-[560px] px-4 py-6 text-sm text-gray-600">Loading voice processing...</div>}>
      <VoiceProcessingClient />
    </Suspense>
  );
}
