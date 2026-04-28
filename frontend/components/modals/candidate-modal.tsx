"use client";

import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useMemo, useState } from "react";

import { useAppContext } from "@/context/AppContext";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Modal } from "@/components/ui/modal";
import { Separator } from "@/components/ui/separator";
import { exportCandidates, getCandidatesWithMode, swipeCandidate } from "@/lib/api/candidates";
import { getOutreachStatuses, sendOutreach } from "@/lib/api/outreach";
import { cn } from "@/lib/utils";
import type { Candidate } from "@/types";

type CandidateModalProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

type PipelineTab = "all" | "shortlisted" | "rejected";

const SWIPE_ACCEPT_THRESHOLD = 120;
const SWIPE_REJECT_THRESHOLD = -120;

function dedupeCandidates(candidates: Candidate[]): Candidate[] {
  const byId = new Map<string, Candidate>();
  for (const candidate of candidates) {
    if (!candidate?.id) continue;
    if (!byId.has(candidate.id)) {
      byId.set(candidate.id, candidate);
      continue;
    }
    const existing = byId.get(candidate.id);
    if (!existing || candidate.fitScore > existing.fitScore) {
      byId.set(candidate.id, candidate);
    }
  }
  return Array.from(byId.values());
}

export function CandidateModal({ open, onOpenChange }: CandidateModalProps) {
  const { candidates, isRefined, jobId, setCandidates } = useAppContext();
  const [pipelineTab, setPipelineTab] = useState<PipelineTab>("all");
  const [deckIndex, setDeckIndex] = useState(0);
  const [expandedById, setExpandedById] = useState<Record<string, boolean>>({});
  const [feedbackMessage, setFeedbackMessage] = useState("");
  const [error, setError] = useState("");
  const [isSyncing, setIsSyncing] = useState(false);
  const [actionLoadingId, setActionLoadingId] = useState("");

  const uniqueCandidates = useMemo(() => dedupeCandidates(candidates), [candidates]);

  const counts = useMemo(
    () => ({
      all: uniqueCandidates.length,
      shortlisted: uniqueCandidates.filter((candidate) =>
        ["shortlisted", "contacted", "exported", "interview_scheduled"].includes(candidate.status)
      ).length,
      rejected: uniqueCandidates.filter((candidate) => candidate.status === "rejected").length
    }),
    [uniqueCandidates]
  );

  const reviewQueue = useMemo(
    () => uniqueCandidates.filter((candidate) => !["rejected", "shortlisted", "contacted", "exported", "interview_scheduled"].includes(candidate.status)),
    [uniqueCandidates]
  );

  const effectiveDeckIndex = Math.min(deckIndex, Math.max(0, reviewQueue.length - 1));
  const currentReviewCandidate = reviewQueue[effectiveDeckIndex] || null;

  const tabCandidates = useMemo(() => {
    if (pipelineTab === "shortlisted") {
      return uniqueCandidates.filter((candidate) =>
        ["shortlisted", "contacted", "exported", "interview_scheduled"].includes(candidate.status)
      );
    }
    if (pipelineTab === "rejected") {
      return uniqueCandidates.filter((candidate) => candidate.status === "rejected");
    }
    return uniqueCandidates;
  }, [pipelineTab, uniqueCandidates]);

  const syncCandidates = useCallback(async ({ refresh = false }: { refresh?: boolean } = {}) => {
    if (!jobId) return;
    setIsSyncing(true);
    const result = await getCandidatesWithMode({ jobId, mode: "volume", refresh });
    if (result.success && result.data) {
      setCandidates(dedupeCandidates(result.data));
    }
    const outreachResult = await getOutreachStatuses(jobId);
    if (outreachResult.success && outreachResult.data) {
      setCandidates(
        dedupeCandidates(
          (result.success && result.data ? result.data : uniqueCandidates).map((candidate) => {
            const found = outreachResult.data?.find((row) => row.candidateId === candidate.id);
            if (!found) return candidate;
            return {
              ...candidate,
              outreachStatus: found.status,
              status:
                found.status === "sent" || found.status === "dry_run"
                  ? "contacted"
                  : candidate.status
            };
          })
        )
      );
    }
    setIsSyncing(false);
  }, [jobId, setCandidates, uniqueCandidates]);

  const getSignals = (candidate: Candidate) => {
    const explanation = candidate.explanation;
    const penalties = explanation?.penalties;
    return {
      semantic: explanation?.semantic ?? explanation?.semanticScore ?? 0,
      skillsMatch: explanation?.skillsMatched || explanation?.skills_match || [],
      experienceMatch: explanation?.experienceMatch || "",
      feedbackBoost: explanation?.feedback_boost ?? penalties?.feedbackBias ?? penalties?.feedbackBonus ?? 0,
      diversityBonus: explanation?.diversity_bonus ?? penalties?.diversityBonus ?? 0,
      explorationBonus: explanation?.exploration_bonus ?? penalties?.explorationBonus ?? 0,
      rejectionPenalty: explanation?.rejection_penalty ?? penalties?.rejectionPenalty ?? 0
    };
  };

  const getWhySelected = (candidate: Candidate): string[] => {
    const signals = getSignals(candidate);
    const reasons: string[] = [];
    if (signals.semantic > 0.7) reasons.push("Strong semantic match");
    if (signals.skillsMatch.length > 0) reasons.push("Matches required skills");
    if (signals.experienceMatch) reasons.push("Experience level aligns");
    if (signals.feedbackBoost > 0) reasons.push("Similar to successful past candidates");
    if (signals.diversityBonus > 0) reasons.push("Adds diversity to candidate pool");
    if (signals.explorationBonus > 0) reasons.push("Discovered via exploration");
    if (reasons.length === 0) reasons.push("Good overall match based on role and skills");
    return reasons.slice(0, 3);
  };

  const hasConcerns = (candidate: Candidate): boolean => {
    const signals = getSignals(candidate);
    const lowSemantic = signals.semantic > 0 && signals.semantic < 0.45;
    const highPenalty = signals.rejectionPenalty > 0.08;
    const hasSkillSignals = candidate.explanation?.skillsMatched !== undefined || candidate.explanation?.skills_match !== undefined;
    const missingSkills = hasSkillSignals && signals.skillsMatch.length === 0;
    return lowSemantic || highPenalty || missingSkills;
  };

  const getTopMatches = (candidate: Candidate) => {
    const signals = getSignals(candidate);
    if (signals.skillsMatch.length > 0) return signals.skillsMatch.slice(0, 3);
    return candidate.skills.slice(0, 3);
  };

  const updateCandidateStatus = (candidateId: string, status: Candidate["status"], extra: Partial<Candidate> = {}) => {
    setCandidates(
      dedupeCandidates(
        uniqueCandidates.map((candidate) =>
          candidate.id === candidateId
            ? { ...candidate, status, ...extra }
            : candidate
        )
      )
    );
  };

  const handleSwipe = async (candidateId: string, action: "accept" | "reject") => {
    if (!jobId) return;
    setActionLoadingId(candidateId);
    setError("");
    setFeedbackMessage("");

    const result = await swipeCandidate({ jobId, candidateId, action });
    if (!result.success || !result.data) {
      setError(result.error || "Could not save feedback.");
      setActionLoadingId("");
      return;
    }

    updateCandidateStatus(candidateId, action === "accept" ? "shortlisted" : "rejected");
    setFeedbackMessage(result.data.message ?? "Feedback recorded.");
    setActionLoadingId("");
    setDeckIndex((prev) => Math.min(prev + 1, Math.max(0, reviewQueue.length - 1)));
    void syncCandidates();
  };

  const handleSingleOutreach = async (candidateId: string) => {
    if (!jobId) return;
    setActionLoadingId(candidateId);
    setError("");
    const result = await sendOutreach({ jobId, selectedCandidates: [candidateId] });
    if (!result.success || !result.data) {
      setError(result.error || "Failed to send outreach.");
      setActionLoadingId("");
      return;
    }
    updateCandidateStatus(candidateId, "contacted", { outreachStatus: "pending" });
    setFeedbackMessage(
      result.data.sent > 0
        ? `Outreach sent to ${result.data.sent} candidate${result.data.sent !== 1 ? "s" : ""}.`
        : "Outreach processed."
    );
    setActionLoadingId("");
    void syncCandidates();
  };

  const handleSingleExport = async (candidateId: string) => {
    if (!jobId) return;
    setActionLoadingId(candidateId);
    setError("");
    const currentCandidate = uniqueCandidates.find((candidate) => candidate.id === candidateId);
    const result = await exportCandidates({ jobId, candidateIds: [candidateId] });
    if (!result.success || !result.data) {
      setError(result.error || "Failed to export candidate.");
      setActionLoadingId("");
      return;
    }
    const rowResult = result.data.results?.[0];
    const nextAtsStatus = rowResult?.status === "sent" ? "sent" : rowResult?.status === "failed" ? "failed" : result.data.status === "sent" ? "sent" : "not_sent";
    updateCandidateStatus(candidateId, nextAtsStatus === "sent" ? "exported" : currentCandidate?.status || "shortlisted", {
      exportStatus: nextAtsStatus === "sent" ? "exported" : nextAtsStatus === "failed" ? "failed" : "pending",
      ats_export_status: nextAtsStatus
    });
    setFeedbackMessage(
      nextAtsStatus === "sent"
        ? "Exported to ATS ✅"
        : nextAtsStatus === "failed"
          ? "Failed to export ❌"
          : "Already exported"
    );
    setActionLoadingId("");
    void syncCandidates();
  };

  const toggleExpanded = (candidateId: string) => {
    setExpandedById((prev) => ({ ...prev, [candidateId]: !prev[candidateId] }));
  };

  const renderExplanation = (candidate: Candidate) => {
    const signals = getSignals(candidate);
    return (
      <div className="space-y-2">
        <div className="space-y-1">
          <p className="text-sm font-medium text-gray-800">Top Matches</p>
          <div className="flex flex-wrap gap-2">
            {getTopMatches(candidate).map((skill) => (
              <span key={`${candidate.id}-${skill}`} className="rounded bg-gray-100 px-2 py-1 text-xs text-gray-700">
                {skill}
              </span>
            ))}
          </div>
        </div>
        <div className="space-y-1">
          <p className="text-sm font-medium text-gray-800">Why selected</p>
          {getWhySelected(candidate).map((reason) => (
            <p key={`${candidate.id}-${reason}`} className="text-xs text-green-700">
              ✔ {reason}
            </p>
          ))}
        </div>
        {signals.experienceMatch && (
          <p className="text-xs text-gray-600">Experience: {signals.experienceMatch}</p>
        )}
        {hasConcerns(candidate) && (
          <div className="rounded-md bg-yellow-50 px-2 py-2 text-xs text-yellow-800">
            ⚠ Potential gaps in experience or skills
          </div>
        )}
        <button
          type="button"
          className="text-xs font-medium text-gray-600 hover:text-gray-900"
          onClick={() => toggleExpanded(candidate.id)}
        >
          {expandedById[candidate.id] ? "View Details ▲" : "View Details ▼"}
        </button>
        {expandedById[candidate.id] && (
          <div className="rounded-md bg-gray-50 p-2 text-xs text-gray-500">
            <p>Semantic: {signals.semantic.toFixed(2)}</p>
            <p>Feedback Boost: {signals.feedbackBoost >= 0 ? "+" : ""}{signals.feedbackBoost.toFixed(2)}</p>
            <p>Diversity: {signals.diversityBonus >= 0 ? "+" : ""}{signals.diversityBonus.toFixed(2)}</p>
            <p>Exploration: {signals.explorationBonus >= 0 ? "+" : ""}{signals.explorationBonus.toFixed(2)}</p>
            <p>Penalties: -{signals.rejectionPenalty.toFixed(2)}</p>
          </div>
        )}
      </div>
    );
  };

  const renderPipelineCard = (candidate: Candidate) => (
    <Card key={candidate.id} className="space-y-2 rounded-[20px] p-4 shadow-[0_4px_12px_rgba(0,0,0,0.02)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-semibold text-gray-900">{candidate.name || candidate.id.slice(0, 8)}</h4>
          <p className="text-xs text-gray-600">
            {candidate.role}
            {candidate.company ? ` @ ${candidate.company}` : ""}
          </p>
        </div>
        <Badge variant={candidate.status === "rejected" ? "low" : candidate.status === "exported" ? "high" : "medium"}>
          {candidate.fitScore}/5
        </Badge>
      </div>
      {renderExplanation(candidate)}
      <div className="flex flex-wrap gap-2 text-xs text-gray-500">
        <span>Status: {candidate.status}</span>
        <span>Outreach: {candidate.outreachStatus || "pending"}</span>
        <span>ATS: {candidate.ats_export_status || "not_sent"}</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Button
          variant="outline"
          className="justify-center"
          onClick={() => handleSingleOutreach(candidate.id)}
          disabled={actionLoadingId === candidate.id}
        >
          {actionLoadingId === candidate.id ? "Sending..." : "Send Outreach"}
        </Button>
        <Button
          className="justify-center"
          onClick={() => handleSingleExport(candidate.id)}
          disabled={actionLoadingId === candidate.id}
        >
          {actionLoadingId === candidate.id ? "Exporting..." : "Export to ATS"}
        </Button>
      </div>
    </Card>
  );

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Top Candidate Matches"
      description="Review, shortlist, outreach, and export candidates"
    >
      <div className="space-y-5">
        {isRefined && (
          <div className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
            Refined based on your input
          </div>
        )}

        <div className="flex items-center justify-between">
          <div className="flex gap-2">
            {(["all", "shortlisted", "rejected"] as PipelineTab[]).map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => setPipelineTab(tab)}
                className={cn(
                  "rounded-full px-3 py-1 text-xs font-medium",
                  pipelineTab === tab ? "bg-[#111111] text-white" : "bg-gray-100 text-gray-600"
                )}
              >
                {tab === "all" ? "All" : tab === "shortlisted" ? "Shortlisted" : "Rejected"} ({counts[tab]})
              </button>
            ))}
          </div>
          <div className="text-xs text-gray-500">{isSyncing ? "Live updating..." : `${uniqueCandidates.length} candidates`}</div>
        </div>
        <Button variant="outline" className="w-full justify-center text-xs" onClick={() => void syncCandidates()} disabled={isSyncing}>
          {isSyncing ? "Refreshing..." : "Refresh Pipeline"}
        </Button>

        {pipelineTab === "all" ? (
          <div className="space-y-3">
            <p className="text-xs text-gray-500">Review Queue: {reviewQueue.length === 0 ? 0 : deckIndex + 1} / {reviewQueue.length}</p>
            <div className="min-h-[360px]">
              <AnimatePresence mode="wait">
                {currentReviewCandidate ? (
                  <motion.div
                    key={currentReviewCandidate.id}
                    drag="x"
                    dragConstraints={{ left: 0, right: 0 }}
                    onDragEnd={(_, info) => {
                      if (info.offset.x >= SWIPE_ACCEPT_THRESHOLD) {
                        void handleSwipe(currentReviewCandidate.id, "accept");
                        return;
                      }
                      if (info.offset.x <= SWIPE_REJECT_THRESHOLD) {
                        void handleSwipe(currentReviewCandidate.id, "reject");
                      }
                    }}
                    initial={{ opacity: 0, scale: 0.98 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.98 }}
                    transition={{ duration: 0.2 }}
                    className="cursor-grab active:cursor-grabbing"
                  >
                    <Card className="space-y-3 rounded-[20px] p-4 shadow-[0_4px_12px_rgba(0,0,0,0.02)]">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <h4 className="text-sm font-semibold text-gray-900">
                            {currentReviewCandidate.name || currentReviewCandidate.id.slice(0, 8)}
                          </h4>
                          <p className="text-xs text-gray-600">
                            {currentReviewCandidate.role}
                            {currentReviewCandidate.company ? ` @ ${currentReviewCandidate.company}` : ""}
                          </p>
                        </div>
                        <Badge variant={currentReviewCandidate.strategy === "HIGH" ? "high" : currentReviewCandidate.strategy === "MEDIUM" ? "medium" : "low"}>
                          ⭐ {currentReviewCandidate.fitScore}
                        </Badge>
                      </div>
                      {renderExplanation(currentReviewCandidate)}
                      <Separator />
                      <div className="grid grid-cols-2 gap-2">
                        <Button
                          variant="outline"
                          className="justify-center"
                          onClick={() => handleSwipe(currentReviewCandidate.id, "reject")}
                          disabled={actionLoadingId === currentReviewCandidate.id}
                        >
                          Reject
                        </Button>
                        <Button
                          className="justify-center"
                          onClick={() => handleSwipe(currentReviewCandidate.id, "accept")}
                          disabled={actionLoadingId === currentReviewCandidate.id}
                        >
                          Accept
                        </Button>
                      </div>
                      <p className="text-center text-xs text-gray-500">Swipe left to reject, right to accept</p>
                    </Card>
                  </motion.div>
                ) : (
                  <Card className="rounded-xl p-6 text-center text-sm text-gray-600">
                    Review queue complete. Use the Shortlisted tab to revisit accepted candidates.
                  </Card>
                )}
              </AnimatePresence>
            </div>
          </div>
        ) : (
          <div className="max-h-[52vh] space-y-3 overflow-y-auto pr-1">
            {tabCandidates.map((candidate) => renderPipelineCard(candidate))}
            {tabCandidates.length === 0 && (
              <Card className="rounded-xl p-4 text-sm text-gray-600">
                No candidates in this pipeline state yet.
              </Card>
            )}
          </div>
        )}

        {feedbackMessage && <p className="text-sm text-gray-700">{feedbackMessage}</p>}
        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="grid gap-2 sm:grid-cols-2">
          <Link
            href="/voice"
            onClick={() => onOpenChange(false)}
            className={cn(buttonVariants({ variant: "default" }), "justify-center")}
          >
            Voice Intake
          </Link>
          <Link
            href="/outreach"
            onClick={() => onOpenChange(false)}
            className={cn(buttonVariants({ variant: "outline" }), "justify-center")}
          >
            Skip to Outreach
          </Link>
        </div>
      </div>
    </Modal>
  );
}
