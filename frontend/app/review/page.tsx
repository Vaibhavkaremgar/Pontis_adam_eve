"use client";

/**
 * What this file does:
 * Full-page swipe/review UI — step 3 in the hiring pipeline.
 * Recruiter accepts or rejects each candidate one at a time.
 * Accepted candidates become shortlisted and then flow into voice intake.
 *
 * What API it connects to:
 * POST /candidates/swipe — records accept/reject + triggers RLHF update
 *
 * How it fits in the pipeline:
 * Job page → Review page (swipe) → Voice page → Outreach page
 */
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { useAppContext } from "@/context/AppContext";
import { swipeCandidate } from "@/lib/api/candidates";
import type { Candidate } from "@/types";

function dedupe(candidates: Candidate[]): Candidate[] {
  const seen = new Map<string, Candidate>();
  for (const c of candidates) {
    if (!c?.id) continue;
    const existing = seen.get(c.id);
    if (!existing || c.fitScore > existing.fitScore) seen.set(c.id, c);
  }
  return Array.from(seen.values());
}

const DECIDED_STATUSES = new Set(["shortlisted", "rejected", "contacted", "exported", "interview_scheduled", "booked"]);
const SWIPE_THRESHOLD = 90;

export default function ReviewPage() {
  const router = useRouter();
  const { user, isSessionReady, jobId, candidates, setCandidates } = useAppContext();

  const [actionLoadingId, setActionLoadingId] = useState("");
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState("");
  const [dragX, setDragX] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const dragRef = useRef({ pointerId: -1, startX: 0 });

  // Auth + flow guard
  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) { router.replace("/login"); return; }
    if (!jobId) { router.replace("/job"); return; }
    if (candidates.length === 0) { router.replace("/job"); }
  }, [isSessionReady, user, jobId, candidates.length, router]);

  // Keyboard shortcuts: → accept, ← reject
  const unique = useMemo(() => dedupe(candidates), [candidates]);
  const queue = useMemo(() => unique.filter((c) => !DECIDED_STATUSES.has(c.status)), [unique]);
  const shortlisted = useMemo(
    () => unique.filter((c) => ["shortlisted", "contacted", "exported", "interview_scheduled", "booked"].includes(c.status)),
    [unique]
  );
  const current = queue[0] ?? null;

  const resetDrag = useCallback(() => {
    dragRef.current = { pointerId: -1, startX: 0 };
    setDragX(0);
    setIsDragging(false);
  }, []);

  useEffect(() => {
    queueMicrotask(resetDrag);
  }, [current?.id, resetDrag]);

  const handleSwipe = useCallback(
    async (candidateId: string, action: "accept" | "reject") => {
      if (!jobId || actionLoadingId) return;
      setActionLoadingId(candidateId);
      setError("");

      const result = await swipeCandidate({ jobId, candidateId, action });
      if (!result.success || !result.data) {
        // 409 = state machine violation (already decided or locked state)
        setError(result.error || "Could not save feedback. Please try again.");
        setActionLoadingId("");
        return;
      }

      // Use the authoritative newState from the backend response
      const nextStatus = (result.data.newState || (action === "accept" ? "shortlisted" : "rejected")) as Candidate["status"];
      const nextAtsExportStatus = result.data.ats_export_status || "not_sent";
      setCandidates(
        dedupe(
          unique.map((c) => (c.id === candidateId ? { ...c, status: nextStatus, ats_export_status: nextAtsExportStatus } : c))
        )
      );
      setExpandedId("");
      setActionLoadingId("");
    },
    [jobId, actionLoadingId, unique, setCandidates]
  );

  const goToVoice = useCallback(() => {
    if (!jobId) return;
    router.push(`/voice?jobId=${encodeURIComponent(jobId)}`);
  }, [jobId, router]);

  const handlePointerDown = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (!current || actionLoadingId) return;
    const target = e.target as HTMLElement | null;
    if (target?.closest("button, a, input, textarea, select, label")) return;

    dragRef.current = { pointerId: e.pointerId, startX: e.clientX };
    setIsDragging(true);
    setDragX(0);

    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // Pointer capture is best-effort.
    }
  }, [actionLoadingId, current]);

  const handlePointerMove = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (!isDragging || dragRef.current.pointerId !== e.pointerId) return;
    setDragX(e.clientX - dragRef.current.startX);
  }, [isDragging]);

  const finishDrag = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current.pointerId !== e.pointerId) return;

    const deltaX = e.clientX - dragRef.current.startX;
    const action = deltaX > SWIPE_THRESHOLD ? "accept" : deltaX < -SWIPE_THRESHOLD ? "reject" : null;
    resetDrag();

    if (!action || !current || actionLoadingId) return;
    void handleSwipe(current.id, action);
  }, [actionLoadingId, current, handleSwipe, resetDrag]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!current || actionLoadingId) return;
      if (e.key === "ArrowRight") void handleSwipe(current.id, "accept");
      if (e.key === "ArrowLeft") void handleSwipe(current.id, "reject");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, actionLoadingId, handleSwipe]);

  const renderSkillBadges = (skills: string[]) =>
    skills.slice(0, 5).map((skill) => (
      <span key={skill} className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
        {skill}
      </span>
    ));

  const renderSignals = (c: Candidate) => {
    const ex = c.explanation;
    const p = ex?.penalties ?? {};
    const semantic = ex?.semanticScore ?? ex?.semantic ?? 0;
    const matchedSkills = ex?.skillsMatched ?? ex?.skills_match ?? [];
    const feedbackBias = p.feedbackBias ?? p.feedbackBonus ?? 0;
    const diversityBonus = p.diversityBonus ?? 0;
    const explorationBonus = p.explorationBonus ?? 0;
    const rejectionPenalty = p.rejectionPenalty ?? 0;
    return (
      <div className="rounded-md bg-gray-50 p-3 text-xs text-gray-500 space-y-1">
        <p>Semantic match: <span className="font-medium text-gray-700">{(semantic * 100).toFixed(0)}%</span></p>
        {(ex?.experienceMatch || ex?.candidateExperience || ex?.jobExperience) && (
          <p>
            Experience: <span className="font-medium text-gray-700">{ex.experienceMatch || "Aligned with role"}</span>
          </p>
        )}
        {matchedSkills.length > 0 && (
          <p>
            Skills matched: <span className="font-medium text-gray-700">{matchedSkills.slice(0, 4).join(", ")}</span>
          </p>
        )}
        {feedbackBias > 0 && <p>Feedback boost: <span className="text-green-700">+{feedbackBias.toFixed(3)}</span></p>}
        {diversityBonus > 0 && <p>Diversity bonus: <span className="text-green-700">+{diversityBonus.toFixed(3)}</span></p>}
        {explorationBonus > 0 && <p>Exploration: <span className="text-blue-600">+{explorationBonus.toFixed(3)}</span></p>}
        {rejectionPenalty > 0 && <p>Rejection penalty: <span className="text-red-500">-{rejectionPenalty.toFixed(3)}</span></p>}
        {ex?.aiReasoning && <p className="italic text-gray-600">AI: {ex.aiReasoning}</p>}
      </div>
    );
  };

  return (
    <AppShell activeStep={3}>
      <div className="mx-auto w-full max-w-[560px] space-y-6">
        {/* Header counts */}
        <div className="flex items-center justify-between text-sm text-gray-600">
          <span>
            Queue: <strong>{queue.length}</strong> remaining
          </span>
          <span>
            Shortlisted: <strong>{shortlisted.length}</strong>
          </span>
        </div>

        {/* Active swipe card */}
        {current ? (
          <div
            className="relative select-none touch-pan-y"
            style={{
              transform: isDragging
                ? `translateX(${Math.max(-120, Math.min(120, dragX))}px) rotate(${Math.max(-8, Math.min(8, dragX / 25))}deg)`
                : undefined,
              transition: isDragging ? "none" : "transform 180ms ease"
            }}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={finishDrag}
            onPointerCancel={finishDrag}
          >
          <Card className="relative shadow-[0_4px_12px_rgba(0,0,0,0.02)]">
            {isDragging && Math.abs(dragX) > 12 && (
              <div
                className={`absolute right-4 top-4 rounded-full px-3 py-1 text-xs font-semibold ${
                  dragX > 0 ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
                }`}
              >
                Release to {dragX > 0 ? "accept" : "reject"}
              </div>
            )}
            <CardHeader className="space-y-1 pb-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="text-lg">{current.name || `Candidate ${current.id.slice(0, 8)}`}</CardTitle>
                  <CardDescription>
                    {current.role}
                    {current.company ? ` · ${current.company}` : ""}
                  </CardDescription>
                </div>
                <Badge
                  variant={
                    current.strategy === "HIGH" ? "high" : current.strategy === "MEDIUM" ? "medium" : "low"
                  }
                >
                  ⭐ {current.fitScore.toFixed(1)} / 5
                </Badge>
              </div>
            </CardHeader>

            <CardContent className="space-y-4">
              {/* Skills */}
              {current.skills.length > 0 && (
                <div className="space-y-1">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Skills</p>
                  <div className="flex flex-wrap gap-1.5">{renderSkillBadges(current.skills)}</div>
                </div>
              )}

              {/* Summary */}
              {current.summary && (
                <div className="space-y-1">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Summary</p>
                  <p className="text-sm text-gray-700 leading-relaxed">{current.summary}</p>
                </div>
              )}

              {/* Expand signals */}
              <button
                type="button"
                className="text-xs text-gray-400 hover:text-gray-700 underline"
                onClick={() => setExpandedId((prev) => (prev === current.id ? "" : current.id))}
              >
                {expandedId === current.id ? "Hide signals ▲" : "Show match signals ▼"}
              </button>
              {expandedId === current.id && renderSignals(current)}

              <Separator />

              {/* Action buttons */}
              <div className="grid grid-cols-2 gap-3">
                <Button
                  variant="outline"
                  className="justify-center border-red-200 text-red-600 hover:bg-red-50 hover:border-red-400"
                  onClick={() => void handleSwipe(current.id, "reject")}
                  disabled={!!actionLoadingId}
                >
                  {actionLoadingId === current.id ? "Saving…" : "✕ Reject"}
                </Button>
                <Button
                  className="justify-center bg-green-700 hover:bg-green-800 text-white"
                  onClick={() => void handleSwipe(current.id, "accept")}
                  disabled={!!actionLoadingId}
                >
                  {actionLoadingId === current.id ? "Saving…" : "✓ Accept"}
                </Button>
              </div>
              <p className="text-center text-xs text-gray-400">
                Keyboard: <kbd className="rounded bg-gray-100 px-1">←</kbd> reject &nbsp;
                <kbd className="rounded bg-gray-100 px-1">→</kbd> accept
              </p>
              <p className="text-center text-xs text-gray-500">
                ATS export: <strong>{current.ats_export_status || "not_sent"}</strong>
              </p>

              {error && <p className="text-sm text-red-600">{error}</p>}
            </CardContent>
          </Card>
          </div>
        ) : (
          /* Queue empty */
          <Card className="p-8 text-center shadow-[0_4px_12px_rgba(0,0,0,0.02)]">
            <p className="text-lg font-semibold text-gray-800 mb-1">All candidates reviewed</p>
            <p className="text-sm text-gray-500 mb-6">
              {shortlisted.length > 0
                ? `You shortlisted ${shortlisted.length} candidate${shortlisted.length > 1 ? "s" : ""}. Ready for voice intake.`
                : "No candidates were shortlisted. Go back to generate a new batch."}
            </p>
            <div className="flex flex-col gap-3">
              {shortlisted.length > 0 && (
                <Button className="w-full justify-center" onClick={goToVoice}>
                  Continue to Voice →
                </Button>
              )}
              <Button variant="outline" className="w-full justify-center" onClick={() => router.push("/job")}>
                ← Back to Job
              </Button>
            </div>
          </Card>
        )}

        {/* Shortlisted sidebar list */}
        {shortlisted.length > 0 && current && (
          <div className="space-y-2">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
              Shortlisted ({shortlisted.length})
            </p>
            {shortlisted.map((c) => (
              <div
                key={c.id}
                className="flex items-center justify-between rounded-lg border border-green-100 bg-green-50 px-3 py-2"
              >
                <div>
                  <p className="text-sm font-medium text-gray-900">{c.name || c.id.slice(0, 8)}</p>
                  <p className="text-xs text-gray-500">{c.role}</p>
                  <p className="text-[11px] text-gray-400">ATS: {c.ats_export_status || "not_sent"}</p>
                </div>
                <Badge variant="high">✓</Badge>
              </div>
            ))}
            <Button
              variant="outline"
              className="w-full justify-center text-sm"
              onClick={goToVoice}
            >
              Continue to Voice →
            </Button>
          </div>
        )}
      </div>
    </AppShell>
  );
}
