"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Play, RefreshCw, VideoOff } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type InterviewRecordingPlayerProps = {
  workflowToken: string;
  title?: string;
  available?: boolean;
  className?: string;
};

type VideoState = "loading" | "ready" | "error" | "unavailable";

export function InterviewRecordingPlayer({ workflowToken, title = "Interview recording", available = true, className }: InterviewRecordingPlayerProps) {
  const [state, setState] = useState<VideoState>(available ? "loading" : "unavailable");
  const [attempt, setAttempt] = useState(0);
  const sourceUrl = useMemo(() => {
    if (!workflowToken) return "";
    return `/api/backend/recording/${encodeURIComponent(workflowToken)}?v=${attempt}`;
  }, [attempt, workflowToken]);

  useEffect(() => {
    setState(available ? "loading" : "unavailable");
  }, [available, workflowToken]);

  if (!workflowToken) {
    return (
      <div className={cn("flex min-h-[320px] items-center justify-center rounded-3xl border border-dashed border-slate-300 bg-slate-50 p-6 text-sm text-slate-600", className)}>
        Select a candidate to load the recording.
      </div>
    );
  }

  if (!available) {
    return (
      <div className={cn("flex min-h-[320px] flex-col items-center justify-center gap-3 rounded-3xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center", className)}>
        <VideoOff className="h-8 w-8 text-slate-400" />
        <div>
          <p className="font-semibold text-slate-900">Recording unavailable</p>
          <p className="mt-1 text-sm text-slate-600">Pontis has not exposed a playable stream for this result yet.</p>
        </div>
      </div>
    );
  }

  return (
    <div className={cn("overflow-hidden rounded-3xl border border-slate-200 bg-black shadow-[0_18px_48px_rgba(15,23,42,0.18)]", className)}>
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3 text-white/90">
        <div>
          <p className="text-sm font-semibold">{title}</p>
          <p className="text-xs text-white/60">Streaming through Adam proxy only</p>
        </div>
        {state === "loading" && <span className="text-xs uppercase tracking-[0.2em] text-white/50">Buffering</span>}
      </div>
      <div className="relative aspect-video bg-[radial-gradient(circle_at_top,_rgba(56,189,248,0.22),_transparent_55%),linear-gradient(135deg,_#020617,_#0f172a_60%,_#111827)]">
        {state !== "ready" && (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="flex flex-col items-center gap-4 text-center text-white">
              {state === "error" ? <AlertCircle className="h-10 w-10 text-rose-300" /> : <Play className="h-10 w-10 text-sky-300" />}
              <div>
                <p className="text-sm font-medium">
                  {state === "loading" ? "Loading secure stream…" : "Playback unavailable"}
                </p>
                <p className="mt-1 max-w-sm text-xs text-white/60">
                  {state === "loading"
                    ? "Adam is proxying the stream from Pontis and keeping the recruiter inside the workspace."
                    : "The stream may not be ready yet, or Pontis returned an error."
                  }
                </p>
              </div>
              {state === "error" && (
                <Button
                  type="button"
                  variant="outline"
                  className="border-white/20 bg-white/5 text-white hover:bg-white/10 hover:text-white"
                  onClick={() => {
                    setState("loading");
                    setAttempt((value) => value + 1);
                  }}
                >
                  <RefreshCw className="mr-2 h-4 w-4" />
                  Retry stream
                </Button>
              )}
            </div>
          </div>
        )}
        <video
          key={sourceUrl}
          className="h-full w-full object-contain"
          controls
          preload="metadata"
          playsInline
          onLoadedData={() => setState("ready")}
          onCanPlay={() => setState("ready")}
          onWaiting={() => setState((current) => (current === "ready" ? "loading" : current))}
          onLoadStart={() => setState("loading")}
          onError={() => setState("error")}
        >
          <source src={sourceUrl} type="video/mp4" />
          Your browser does not support the video tag.
        </video>
      </div>
    </div>
  );
}
