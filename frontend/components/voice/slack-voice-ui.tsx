"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Vapi from "@vapi-ai/web";
import { Mic, PhoneOff, Sparkles } from "lucide-react";

import { ChatBubble } from "./chat-bubble";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { completeOrchestrationVoice, startOrchestrationVoice, type OrchestrationVoiceStartData } from "@/lib/api/orchestration";

type TranscriptRole = "assistant" | "user";
type TranscriptTurn = {
  id: string;
  role: TranscriptRole;
  speaker: "Adam" | "Recruiter";
  finalTranscript: string;
  liveTranscript: string;
  isStreaming: boolean;
  isFinal: boolean;
  timestamp: string;
};

function normalize(value: string) {
  return value.trim().replace(/\s+/g, " ");
}

function turnText(turn: TranscriptTurn) {
  return normalize(turn.isFinal ? turn.finalTranscript : turn.liveTranscript || turn.finalTranscript);
}

function extractTranscriptEvent(message: unknown): { role: TranscriptRole; text: string; isFinal: boolean } | null {
  if (!message || typeof message !== "object") return null;
  const record = message as Record<string, unknown>;
  if (record.type !== "transcript") return null;
  const text = normalize(String(record.transcript || ""));
  if (!text) return null;
  const role: TranscriptRole = record.role === "assistant" ? "assistant" : "user";
  const isFinal = record.transcriptType === "final" || record.isFinal === true || record.final === true;
  return { role, text, isFinal };
}

function updateTurns(turns: TranscriptTurn[], role: TranscriptRole, text: string, isFinal: boolean): TranscriptTurn[] {
  const normalized = normalize(text);
  if (!normalized) return turns;
  const next = [...turns];
  const last = next[next.length - 1];
  if (last?.role === role) {
    next[next.length - 1] = {
      ...last,
      finalTranscript: isFinal ? normalized : last.finalTranscript,
      liveTranscript: isFinal ? normalized : normalized,
      isStreaming: !isFinal,
      isFinal: last.isFinal || isFinal,
      timestamp: new Date().toISOString(),
    };
    return next;
  }
  next.push({
    id: `${role}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role,
    speaker: role === "assistant" ? "Adam" : "Recruiter",
    finalTranscript: normalized,
    liveTranscript: normalized,
    isStreaming: !isFinal,
    isFinal,
    timestamp: new Date().toISOString(),
  });
  return next;
}

function buildTranscript(turns: TranscriptTurn[]) {
  return turns
    .map((turn) => `${turn.role === "assistant" ? "Adam" : "Recruiter"}: ${turnText(turn)}`)
    .join("\n");
}

async function loadVapiConfig(): Promise<{ assistantId: string; publicKey: string }> {
  const response = await fetch("/api/vapi/config", { method: "GET" });
  const json = (await response.json()) as { success?: boolean; data?: { assistantId?: string; publicKey?: string } };
  return {
    assistantId: String(json.data?.assistantId || "").trim(),
    publicKey: String(json.data?.publicKey || "").trim(),
  };
}

export function SlackVoiceUi({ token }: { token: string }) {
  const router = useRouter();
  const vapiRef = useRef<Vapi | null>(null);
  const startedRef = useRef(false);
  const turnsRef = useRef<TranscriptTurn[]>([]);
  const [status, setStatus] = useState<"loading" | "connecting" | "listening" | "speaking" | "processing" | "completed" | "error">("loading");
  const [error, setError] = useState("");
  const [session, setSession] = useState<OrchestrationVoiceStartData | null>(null);
  const [assistantId, setAssistantId] = useState("");
  const [publicKey, setPublicKey] = useState("");
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const bootstrap = async () => {
      try {
        const [vapiConfig, sessionResult] = await Promise.all([loadVapiConfig(), startOrchestrationVoice(token)]);
        if (cancelled) return;
        if (!sessionResult.success || !sessionResult.data) {
          setStatus("error");
          setError(sessionResult.error || "Could not start Slack voice handoff.");
          return;
        }
        setAssistantId(vapiConfig.assistantId);
        setPublicKey(vapiConfig.publicKey);
        setSession(sessionResult.data);
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setError(err instanceof Error ? err.message : "Failed to load Slack voice session.");
      }
    };
    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const question = useMemo(() => session?.currentQuestion || session?.firstMessage || "Let's continue the intake.", [session]);

  useEffect(() => {
    if (!session || !assistantId || !publicKey || startedRef.current) return;
    let cancelled = false;

    const startCall = async () => {
      try {
        startedRef.current = true;
        const vapi = new Vapi(publicKey);
        vapiRef.current = vapi;

        vapi.on("call-start", () => {
          if (!cancelled) setStatus("listening");
        });
        vapi.on("speech-start", () => {
          if (!cancelled) setStatus("speaking");
        });
        vapi.on("speech-end", () => {
          if (!cancelled) setStatus("listening");
        });
        vapi.on("message", (message) => {
          const event = extractTranscriptEvent(message);
          if (!event || cancelled) return;
          turnsRef.current = updateTurns(turnsRef.current, event.role, event.text, event.isFinal);
          setTurns([...turnsRef.current]);
        });
        vapi.on("error", (errorEvent) => {
          if (cancelled) return;
          setStatus("error");
          setError(`Voice assistant error: ${String((errorEvent as Record<string, unknown>)?.message || "unknown")}`);
        });
        vapi.on("call-end", async () => {
          if (cancelled) return;
          setStatus("processing");
          const transcript = buildTranscript(turnsRef.current);
          setIsSubmitting(true);
          const completion = await completeOrchestrationVoice(token, {
            transcript,
            voiceNotes: turnsRef.current.map((turn) => turnText(turn)).filter(Boolean),
          });
          setIsSubmitting(false);
          if (!completion.success || !completion.data) {
            setStatus("error");
            setError(completion.error || "Could not finish the Slack voice intake.");
            return;
          }
          if (completion.data.completed) {
            setStatus("completed");
            return;
          }
          setStatus("completed");
          setError(completion.data.nextQuestion ? `Next step saved: ${completion.data.nextQuestion}` : "More intake is still pending in Slack.");
        });

        setStatus("connecting");
        await vapi.start(assistantId, {
          firstMessage: session.firstMessage || question,
          variableValues: session.variableValues || {},
        });
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setError(err instanceof Error ? err.message : "Unable to start Slack voice intake.");
      }
    };

    void startCall();
    return () => {
      cancelled = true;
    };
  }, [assistantId, publicKey, question, session, token]);

  useEffect(() => {
    return () => {
      vapiRef.current?.stop().catch(() => undefined);
    };
  }, []);

  const retry = async () => {
    setError("");
    setStatus("loading");
    startedRef.current = false;
    turnsRef.current = [];
    setTurns([]);
    setSession(null);
    try {
      const [vapiConfig, sessionResult] = await Promise.all([loadVapiConfig(), startOrchestrationVoice(token)]);
      if (!sessionResult.success || !sessionResult.data) {
        setStatus("error");
        setError(sessionResult.error || "Could not restart Slack voice handoff.");
        return;
      }
      setAssistantId(vapiConfig.assistantId);
      setPublicKey(vapiConfig.publicKey);
      setSession(sessionResult.data);
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : "Unable to retry Slack voice intake.");
    }
  };

  const handleStop = async () => {
    setIsSubmitting(true);
    try {
      await vapiRef.current?.stop();
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-6">
      <Card className="overflow-hidden border-[#E7E0D4] bg-gradient-to-b from-white to-[#FBF8F2] shadow-[0_16px_40px_rgba(0,0,0,0.06)]">
        <CardHeader className="border-b border-[#EEE3D4] bg-[linear-gradient(135deg,#0F6B3A_0%,#14532D_100%)] text-white">
          <div className="flex items-center justify-between gap-4">
            <div>
              <CardTitle className="flex items-center gap-2 text-white">
                <Sparkles className="h-5 w-5" />
                Chat with Adam
              </CardTitle>
              <p className="mt-1 text-sm text-white/80">Continue the hiring conversation without leaving the Adam experience.</p>
            </div>
            <div className="rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-white/90">
              {status}
            </div>
          </div>
        </CardHeader>

        <CardContent className="space-y-4 p-4 sm:p-6">
          <div className="rounded-2xl border border-[#E7E0D4] bg-white p-4 text-sm text-[#4B5563]">
            <p className="font-semibold text-[#111827]">Current prompt</p>
            <p className="mt-1 leading-6">{session?.currentQuestion || question}</p>
          </div>

          <div className="max-h-[56vh] space-y-3 overflow-y-auto rounded-[24px] border border-[#E7E0D4] bg-[linear-gradient(180deg,#FFFDF9_0%,#F7F2E8_100%)] p-3 shadow-[0_8px_22px_rgba(0,0,0,0.04)]">
            {turns.length > 0 ? (
              turns.map((message) => (
                <ChatBubble
                  key={message.id}
                  message={{
                    id: message.id,
                    role: message.role,
                    speaker: message.speaker,
                    content: turnText(message),
                    isStreaming: message.isStreaming,
                    isFinal: message.isFinal,
                    timestamp: message.timestamp,
                  }}
                  isInterim={message.isStreaming}
                />
              ))
            ) : (
              <div className="rounded-[20px] border border-dashed border-[#D8CCBA] bg-white/70 px-4 py-6 text-center text-sm text-[#6B7280]">
                Adam will continue the intake here and keep the conversation moving.
              </div>
            )}
          </div>

          {error && (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          <div className="flex flex-col gap-3 sm:flex-row sm:justify-between">
            <div className="flex gap-2">
              <Button variant="outline" onClick={retry} disabled={status === "connecting" || isSubmitting}>
                Retry
              </Button>
              <Button onClick={handleStop} disabled={status === "completed" || status === "error" || isSubmitting}>
                <PhoneOff className="mr-2 h-4 w-4" />
                End call
              </Button>
              {status !== "error" && (
                <Button className="bg-[#14532D] hover:bg-[#0F3F23]" onClick={() => router.push("/review")} disabled>
                  <Mic className="mr-2 h-4 w-4" />
                  Intake in progress
                </Button>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
