"use client";

/**
 * What this file does:
 * Tracks selected candidates through outreach, replies, and interview progression from the PDF.
 *
 * What API it connects to:
 * GET /interviews?jobId=...
 * POST /candidates/export
 * GET /interview/insights
 * POST /interview/evaluations
 * POST /interview/decision
 */
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Textarea } from "@/components/ui/textarea";
import { useAppContext } from "@/context/AppContext";
import { exportCandidates, getCandidateEnrichmentState, getFinalSelectionResults, getShortlistedCandidates } from "@/lib/api/candidates";
import {
  getInterviewInsights,
  getInterviewStatuses,
  recordInterviewEvaluation,
  submitInterviewDecision,
  type InterviewInsights,
} from "@/lib/api/interviews";
import { getMetrics } from "@/lib/api/metrics";
import { getOutreachStatuses, sendOutreach, type OutreachStatusItem } from "@/lib/api/outreach";
import { isSuperAdminRole } from "@/lib/roles";
import type { Candidate, InterviewStatus } from "@/types";

type ReadyCandidate = Candidate & {
  candidateId: string;
  status: InterviewStatus["status"] | "enrichment_pending" | "enrichment_no_email";
};

const STATUS_LABELS: Record<string, string> = {
  reviewed: "Reviewed",
  selected: "Selected",
  enrichment_pending: "Enrichment Pending",
  enrichment_no_email: "Enrichment No Email",
  interview_scheduled: "Interview Scheduled",
  interview_requested: "Interview Requested",
  interview_completed: "Interview Completed",
  interview_no_show: "No-show",
  no_show: "No-show",
  rejected: "Rejected",
  advanced: "Advanced",
  final_round: "Final Round",
  offer_sent: "Offer Sent",
  hired: "Placed",
  archived: "Archived",
};

function formatStatus(status: InterviewStatus["status"] | string | null | undefined): string {
  const normalized = (status || "Unknown").toString().trim();
  if (!normalized) return "Unknown";
  return STATUS_LABELS[normalized] || normalized.replace(/_/g, " ");
}

function resolveCandidateName(candidate: Pick<Candidate, "id" | "name" | "profileData"> | null | undefined): string {
  if (!candidate) return "Unnamed candidate";
  const profileData = candidate.profileData || {};
  const rawName =
    candidate.name ||
    String(profileData.full_name || profileData.fullName || profileData.name || profileData.candidate_name || profileData.candidateName || "").trim();
  return rawName || candidate.id || "Unnamed candidate";
}

function normalizeReadyStatus(status: string | null | undefined): ReadyCandidate["status"] {
  const normalized = (status || "selected").toString().trim().toLowerCase();
  const allowed: ReadonlySet<string> = new Set([
    "selected",
    "enrichment_pending",
    "enrichment_no_email",
    "outreach_pending",
    "outreach_sent",
    "interview_requested",
    "interview_scheduled",
    "interview_in_progress",
    "rejected",
    "advanced",
    "second_round_requested",
    "second_round_scheduled",
    "second_round_reschedule_requested",
    "final_round",
    "offer_stage",
    "offer_sent",
    "placed",
    "hired",
    "search_closed",
    "archived",
    "interview_completed",
    "interview_no_show",
    "new",
    "reviewed",
    "evaluation_processing",
    "results_ready",
  ]);
  return (allowed.has(normalized) ? normalized : "selected") as ReadyCandidate["status"];
}

function statusVariant(status: ReadyCandidate["status"]) {
  if (["interview_scheduled", "advanced", "final_round", "hired"].includes(status)) return "high";
  if (["interview_requested", "offer_sent"].includes(status)) return "info";
  if (["outreach_sent", "selected", "interview_completed", "outreach_pending"].includes(status)) return "medium";
  if (["enrichment_pending", "enrichment_no_email"].includes(status)) return "neutral";
  if (["rejected", "archived", "interview_no_show", "no_show"].includes(status)) return "low";
  return "neutral";
}

function ReadyPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, isSessionReady, jobId, setJobId } = useAppContext();
  const queryJobId = String(searchParams.get("jobId") || "").trim();
  const effectiveJobId = jobId || queryJobId;
  const [items, setItems] = useState<ReadyCandidate[]>([]);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const [isExporting, setIsExporting] = useState(false);
  const [outreachMessage, setOutreachMessage] = useState("");
  const [isOutreachSending, setIsOutreachSending] = useState(false);
  const [outreachStatuses, setOutreachStatuses] = useState<OutreachStatusItem[]>([]);
  const [metrics, setMetrics] = useState<{
    emails_sent: number;
    replies_received: number;
    interviews_booked: number;
    conversion_rate: number;
  } | null>(null);

  const [activeCandidate, setActiveCandidate] = useState<ReadyCandidate | null>(null);
  const [activeInsights, setActiveInsights] = useState<InterviewInsights | null>(null);
  const [workflowLoading, setWorkflowLoading] = useState(false);
  const [workflowMessage, setWorkflowMessage] = useState("");
  const [workflowError, setWorkflowError] = useState("");
  const [evaluationSummary, setEvaluationSummary] = useState("");
  const [decisionNote, setDecisionNote] = useState("");
  const [scheduleMode, setScheduleMode] = useState("2nd round");
  const [interviewer, setInterviewer] = useState("");
  const [slotDate, setSlotDate] = useState("");
  const [slotOptions, setSlotOptions] = useState("");
  const [inviteNote, setInviteNote] = useState("");
  const activeCandidateDetail = activeCandidate as Record<string, unknown> | null;

  const enrichmentPollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const orderedItems = useMemo(() => {
    const priority: Record<string, number> = {
      enrichment_pending: 0,
      enrichment_no_email: 1,
      outreach_pending: 2,
      outreach_sent: 3,
      interview_requested: 4,
      interview_scheduled: 5,
      advanced: 6,
      final_round: 7,
      offer_sent: 8,
      hired: 9,
      selected: 10,
      rejected: 11,
      archived: 12,
    };

    return [...items].sort((a, b) => {
      const aRank = priority[a.status] ?? 20;
      const bRank = priority[b.status] ?? 20;
      if (aRank !== bRank) return aRank - bRank;
      return a.name.localeCompare(b.name) || a.candidateId.localeCompare(b.candidateId);
    });
  }, [items]);

  const resultReadyCount = useMemo(
    () => orderedItems.filter((item) => ["interview_completed", "evaluation_processing", "results_ready"].includes(item.status)).length,
    [orderedItems],
  );
  const completedResultCount = useMemo(
    () => orderedItems.filter((item) => String(item.status) === "results_ready").length,
    [orderedItems],
  );

  const itemByCandidateId = useMemo(() => {
    return new Map(orderedItems.map((item) => [item.candidateId, item]));
  }, [orderedItems]);

  const enrichmentPendingItems = useMemo(
    () => orderedItems.filter((item) => ["enrichment_pending", "enrichment_no_email"].includes(item.status)),
    [orderedItems],
  );
  const primaryReadyItems = useMemo(
    () => orderedItems.filter((item) => !["enrichment_pending", "enrichment_no_email", "outreach_sent"].includes(item.status)),
    [orderedItems],
  );

  // Poll enrichment state for any pending-enrichment candidates every 10s
  const pollEnrichmentStates = async () => {
    if (!effectiveJobId) return;
    const pendingItems = items.filter((item) =>
      ["enrichment_pending", "enrichment_no_email", "selected"].includes(item.status) &&
      !item.contactEmail
    );
    if (pendingItems.length === 0) return;
    const updates = await Promise.all(
      pendingItems.map(async (item) => {
        const result = await getCandidateEnrichmentState(effectiveJobId, item.candidateId);
        if (result.success && result.data) {
          return { candidateId: item.candidateId, enrichmentState: result.data };
        }
        return null;
      })
    );
    const resolvedUpdates = updates.filter(Boolean) as Array<{
      candidateId: string;
      enrichmentState: { enrichment_state: string; enrichment_requested_at?: string; enrichment_completed_at?: string };
    }>;
    if (resolvedUpdates.length > 0) {
      setItems((prev) =>
        prev.map((item) => {
          const update = resolvedUpdates.find((u) => u.candidateId === item.candidateId);
          if (!update) return item;
          const newEnrichmentState = update.enrichmentState.enrichment_state;
          const isNowEnriched = ["enriched", "missing_email"].includes(newEnrichmentState);
          return {
            ...item,
            enrichmentStatus: newEnrichmentState,
            status: isNowEnriched ? normalizeReadyStatus("outreach_pending") : item.status,
          };
        })
      );
    }
  };

  useEffect(() => {
    const hasPending = items.some(
      (item) => ["enrichment_pending", "enrichment_no_email"].includes(item.status)
    );
    if (enrichmentPollTimerRef.current) {
      clearTimeout(enrichmentPollTimerRef.current);
      enrichmentPollTimerRef.current = null;
    }
    if (!hasPending) return;
    enrichmentPollTimerRef.current = setTimeout(() => {
      void pollEnrichmentStates();
    }, 10000);
    return () => {
      if (enrichmentPollTimerRef.current) clearTimeout(enrichmentPollTimerRef.current);
    };
  }, [items, effectiveJobId]);

  const loadReady = async () => {
    if (!effectiveJobId || !user) return;
    if (isSuperAdminRole(user.role)) {
      router.replace("/admin");
      return;
    }
    const canViewOperationalMetrics = false;
    setIsLoading(true);
    setError("");
    const [shortlistResult, finalSelectionResult, interviewResult, outreachResult] = await Promise.all([
      getShortlistedCandidates(effectiveJobId),
      getFinalSelectionResults(effectiveJobId),
      getInterviewStatuses(effectiveJobId),
      getOutreachStatuses(effectiveJobId),
    ]);
    const metricsResult = canViewOperationalMetrics ? await getMetrics() : null;
    const shortlistedCandidates = shortlistResult.success && shortlistResult.data ? shortlistResult.data : [];
    const finalSelectionCandidates =
      finalSelectionResult.success && finalSelectionResult.data
        ? (finalSelectionResult.data.topCandidates?.length ? finalSelectionResult.data.topCandidates : finalSelectionResult.data.finalCandidates || [])
        : [];
    const candidateSource = finalSelectionCandidates.length > 0 ? finalSelectionCandidates : shortlistedCandidates;
    if (candidateSource.length === 0) {
      setError(shortlistResult.error || finalSelectionResult.error || "Could not load shortlisted candidates.");
    } else {
      const statusMap = new Map(
        (interviewResult.success && interviewResult.data ? interviewResult.data : []).map((row) => [row.candidateId, row.status] as const)
      );
      const outreachStatusMap = new Map(
        (outreachResult.success && outreachResult.data ? outreachResult.data : []).map((row) => [row.candidateId, row.status] as const)
      );
      setItems(
        candidateSource
          .map((candidate) => {
            const candidateId = candidate.id;
            const interviewStatus = normalizeReadyStatus(statusMap.get(candidateId) || candidate.status || "selected");
            const outreachStatus = outreachStatusMap.get(candidateId);
            const enrichmentStatus = String(candidate.enrichmentStatus || "").trim().toLowerCase();
            let status: ReadyCandidate["status"] = normalizeReadyStatus(interviewStatus);
            if (outreachStatus === "sent") {
              status = "outreach_sent";
            } else if (["enrichment_pending", "enrichment_no_email"].includes(enrichmentStatus)) {
              status = enrichmentStatus as ReadyCandidate["status"];
            } else if (outreachStatus === "queued" || outreachStatus === "pending") {
              status = "outreach_pending";
            }
            return {
              ...candidate,
              name: resolveCandidateName(candidate),
              candidateId,
              status: outreachStatus === "failed" ? "rejected" : status,
            };
          })
          .filter((candidate, index, array) => array.findIndex((item) => item.candidateId === candidate.candidateId) === index)
      );
    }
    if (outreachResult.success && outreachResult.data) {
      setOutreachStatuses(outreachResult.data);
    }
    if (metricsResult && metricsResult.success && metricsResult.data) {
      setMetrics({
        emails_sent: metricsResult.data.emails_sent,
        replies_received: metricsResult.data.replies_received,
        interviews_booked: metricsResult.data.interviews_booked,
        conversion_rate: metricsResult.data.conversion_rate,
      });
    }
    if (!canViewOperationalMetrics) setMetrics(null);
    setIsLoading(false);
  };

  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    if (isSuperAdminRole(user.role)) {
      router.replace("/admin");
      return;
    }
    if (!effectiveJobId) {
      router.replace("/job");
      return;
    }
    const timer = window.setTimeout(() => {
      void loadReady();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [effectiveJobId, isSessionReady, router, user]);

  useEffect(() => {
    if (jobId || !queryJobId) return;
    setJobId(queryJobId);
  }, [jobId, queryJobId, setJobId]);

  const refreshWorkflow = async (candidate: ReadyCandidate) => {
    if (!effectiveJobId) return;
    setWorkflowLoading(true);
    setWorkflowError("");
    const result = await getInterviewInsights(effectiveJobId, candidate.candidateId);
    if (!result.success || !result.data) {
      setWorkflowError(result.error || "Could not load candidate status details.");
      setActiveInsights(null);
    } else {
      setActiveInsights(result.data);
    }
    setWorkflowLoading(false);
  };

  const openWorkflow = (candidate: ReadyCandidate) => {
    setActiveCandidate(candidate);
    setWorkflowMessage("");
    setWorkflowError("");
    setEvaluationSummary("");
    setDecisionNote("");
    setInviteNote("");
    void refreshWorkflow(candidate);
  };

  const handleExport = async () => {
    if (!effectiveJobId || isExporting) return;
    setIsExporting(true);
    setExportMessage("");
    const candidateIds = orderedItems.filter((item) => !["rejected", "archived"].includes(item.status)).map((item) => item.candidateId);
    const result = await exportCandidates({ jobId: effectiveJobId, candidateIds });
    if (!result.success || !result.data) {
      setExportMessage(result.error || "Failed to export candidates.");
      setIsExporting(false);
      return;
    }
    setExportMessage(`Export ${result.data.status}: ${result.data.exportedCount} candidate${result.data.exportedCount !== 1 ? "s" : ""} (ref: ${result.data.reference})`);
    setIsExporting(false);
  };

  const handleSendOutreach = async () => {
    if (!effectiveJobId || isOutreachSending) return;
    setIsOutreachSending(true);
    setOutreachMessage("");
    const candidateIds = orderedItems
      .filter((item) => !["rejected", "archived"].includes(item.status))
      .map((item) => item.candidateId);
    if (candidateIds.length === 0) {
      setOutreachMessage("No shortlisted candidates are available to send outreach.");
      setIsOutreachSending(false);
      return;
    }

    const result = await sendOutreach({ jobId: effectiveJobId, selectedCandidates: candidateIds });
    if (!result.success || !result.data) {
      setOutreachMessage(result.error || "Failed to send outreach.");
      setIsOutreachSending(false);
      return;
    }

    setOutreachMessage(
      `Outreach ${result.data.success ? "queued" : "processed"}: ${result.data.sent} sent, ${result.data.skipped} skipped.`
    );
    await loadReady();
    setIsOutreachSending(false);
  };

  const runDecision = async (action: string, targetStage = "") => {
    if (!effectiveJobId || !activeCandidate || workflowLoading) return;
    setWorkflowLoading(true);
    setWorkflowMessage("");
    setWorkflowError("");
    const schedulingNote =
      action === "advance" && targetStage === "technical_round"
        ? [
            decisionNote,
            `Scheduling mode: ${scheduleMode}`,
            `Interviewer: ${interviewer}`,
            `Preferred date: ${slotDate}`,
            `Slots: ${slotOptions}`,
            `Invite note: ${inviteNote}`,
          ]
            .filter(Boolean)
            .join("\n")
        : decisionNote;
    const result = await submitInterviewDecision({
      jobId: effectiveJobId,
      candidateId: activeCandidate.candidateId,
      action,
      targetStage,
      notes: schedulingNote,
      interviewerId: interviewer,
      recommendation: action,
      sourceType: "adam",
    });
    if (!result.success) {
      setWorkflowError(result.error || "Could not update candidate status.");
    } else {
      setWorkflowMessage("Candidate status updated.");
      await refreshWorkflow(activeCandidate);
      await loadReady();
    }
    setWorkflowLoading(false);
  };

  const markPreScreenComplete = async () => {
    if (!effectiveJobId || !activeCandidate || workflowLoading) return;
    setWorkflowLoading(true);
    setWorkflowMessage("");
    setWorkflowError("");
    const stageName = String(activeInsights?.currentStage || "recruiter_screen");
    const result = await recordInterviewEvaluation({
      jobId: effectiveJobId,
      candidateId: activeCandidate.candidateId,
      stageName,
      summary: evaluationSummary || "Pre-screen completed. Full video/profile processing can be attached when the recorder is wired.",
      recommendation: "review_ready",
      notes: evaluationSummary,
      metadata: {
        videoStatus: "pending_integration",
        profileCardStatus: "generated_from_available_signals",
        pdfStep: "07_pre_screening_interview_completed",
      },
    });
    if (!result.success) {
      setWorkflowError(result.error || "Could not record pre-screen completion.");
    } else {
      setWorkflowMessage("Pre-screen marked complete. Recruiter decision is ready.");
      await refreshWorkflow(activeCandidate);
    }
    setWorkflowLoading(false);
  };

  const profileSummaryLines = useMemo(() => {
    const payload = activeInsights?.workflowPayload || {};
    const intelligence = activeInsights?.intelligence || {};
    const evaluations = activeInsights?.evaluations || [];
    const latestEvaluation = evaluations[0] as Record<string, unknown> | undefined;
    return [
      String(latestEvaluation?.summary || intelligence.summary || payload.summary || "Candidate has moved into the post-outreach status tracking."),
      String(intelligence.recommendationSignal || payload.recommendation || "Adam is tracking stage progression, reply context, and recruiter decisions."),
      String(payload.resumeText || payload.resume_text || "Resume/video profile appears here once candidate reply and interview recorder data are available."),
    ].filter(Boolean).slice(0, 3);
  }, [activeInsights]);

  return (
    <AppShell activeStep={5}>
      <Card className="mx-auto w-full max-w-[760px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Shortlisted candidates ready for outreach</CardTitle>
          <CardDescription>Adam tracks shortlisted candidates here, keeps names attached, and lets you send outreach from one place.</CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {isLoading && <p className="text-sm text-gray-600">Loading shortlisted candidates...</p>}

          {!isLoading && !error && items.length === 0 && (
            <div className="rounded-xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4 text-sm text-gray-600">
              No shortlisted candidates yet. Adam is still processing selections or the shortlist has not been saved yet.
            </div>
          )}

          {primaryReadyItems.map((item) => (
            <div key={item.candidateId} className="space-y-3 rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] p-4">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="font-semibold text-gray-900">{resolveCandidateName(item)}</p>
                </div>
                <Badge variant={statusVariant(item.status)}>{formatStatus(item.status)}</Badge>
              </div>
              <Button className="w-full justify-center" variant="outline" disabled={isLoading} onClick={() => openWorkflow(item)}>
                View status
              </Button>
            </div>
          ))}

          {enrichmentPendingItems.length > 0 && (
            <div className="space-y-3 rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#EEF7F0] p-4">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="font-semibold text-gray-900">Enrichment pending</p>
                  <p className="text-xs text-gray-500">Waiting for LinkedIn enrichment before outreach is queued.</p>
                </div>
                <Badge variant="neutral">{enrichmentPendingItems.length} pending</Badge>
              </div>

              {enrichmentPendingItems.map((item) => (
                <div key={item.candidateId} className="space-y-2 rounded-xl border border-white/70 bg-white/80 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="font-medium text-gray-900">{resolveCandidateName(item)}</p>
                    <Badge variant={statusVariant(item.status)}>{formatStatus(item.status)}</Badge>
                  </div>
                  <p className="text-xs text-gray-500">Email: {item.contactEmail || "pending"}</p>
                  <p className="text-xs text-gray-500">Enrichment source: {item.enrichmentSource || "pending"}</p>
                </div>
              ))}
            </div>
          )}

          <div className="space-y-3 rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4">
            <div className="flex items-center justify-between gap-2">
              <div>
                <p className="font-semibold text-gray-900">Contacted candidates</p>
                <p className="text-xs text-gray-500">Tracks email, reply, follow-up, booking, and re-engagement progress</p>
              </div>
              <Badge variant="neutral">{outreachStatuses.length} records</Badge>
            </div>

            {outreachStatuses.length === 0 ? (
              <p className="text-sm text-gray-600">No automation updates yet.</p>
            ) : (
              outreachStatuses.slice(0, 6).map((item) => {
                const readyItem = itemByCandidateId.get(item.candidateId);
                return (
                  <div key={item.candidateId} className="flex items-center justify-between rounded-xl border border-white/60 bg-white/70 px-3 py-2">
                    <div>
                      <p className="text-sm font-medium text-gray-900">{resolveCandidateName(readyItem)}</p>
                      {readyItem?.status && <p className="text-xs text-gray-500">Candidate status: {formatStatus(readyItem.status)}</p>}
                      <p className="text-xs text-gray-500">{item.toEmail || "No email on file"}</p>
                      {item.replyState && <p className="text-xs text-slate-700">Reply state: {item.replyState.replace(/_/g, " ")}</p>}
                      {item.nextFollowUpAt && <p className="text-xs text-slate-700">Next follow-up: {new Date(item.nextFollowUpAt).toLocaleDateString()}</p>}
                      {item.archiveReason && <p className="text-xs text-slate-700">Archive reason: {item.archiveReason.replace(/_/g, " ")}</p>}
                      <p className="text-xs text-slate-500">
                        Engagement {((item.engagementScore ?? 0) * 100).toFixed(0)}% | Reply likelihood {((item.replyLikelihoodScore ?? 0) * 100).toFixed(0)}% | Responsiveness {((item.responsivenessScore ?? 0) * 100).toFixed(0)}%
                      </p>
                      {item.lastError && <p className="text-xs text-amber-700">Reason: {item.lastError}</p>}
                    </div>
                    <Badge variant={item.status === "sent" ? "high" : item.status === "simulated" ? "info" : item.status === "failed" ? "low" : "neutral"}>
                      {item.status}
                    </Badge>
                  </div>
                );
              })
            )}
          </div>

          {metrics && (
            <div className="grid grid-cols-2 gap-3 rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4 text-sm">
              <div>
                <p className="text-gray-500">Emails sent</p>
                <p className="font-semibold text-gray-900">{metrics.emails_sent}</p>
              </div>
              <div>
                <p className="text-gray-500">Replies received</p>
                <p className="font-semibold text-gray-900">{metrics.replies_received}</p>
              </div>
              <div>
                <p className="text-gray-500">Interviews scheduled</p>
                <p className="font-semibold text-gray-900">{metrics.interviews_booked}</p>
              </div>
              <div>
                <p className="text-gray-500">Conversion</p>
                <p className="font-semibold text-gray-900">{(metrics.conversion_rate * 100).toFixed(0)}%</p>
              </div>
            </div>
          )}

          {error && <p className="text-sm text-red-600">{error}</p>}

          <Button className="w-full justify-center" onClick={handleExport} disabled={isLoading || isExporting || orderedItems.length === 0}>
            {isExporting ? "Exporting..." : "Export to ATS"}
          </Button>
          {exportMessage && <p className="text-sm text-gray-700">{exportMessage}</p>}

          <Button className="w-full justify-center" variant="outline" onClick={handleSendOutreach} disabled={isLoading || isOutreachSending || orderedItems.length === 0}>
            {isOutreachSending ? "Sending outreach..." : "Send outreach"}
          </Button>
          {outreachMessage && <p className="text-sm text-gray-700">{outreachMessage}</p>}

          <div className="grid gap-3 rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F8F5EE] p-4 text-sm text-gray-700">
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-xl bg-white/80 p-3">
                <p className="text-xs uppercase tracking-[0.16em] text-gray-500">Results-ready</p>
                <p className="mt-1 text-xl font-semibold text-gray-900">{resultReadyCount}</p>
              </div>
              <div className="rounded-xl bg-white/80 p-3">
                <p className="text-xs uppercase tracking-[0.16em] text-gray-500">Results complete</p>
                <p className="mt-1 text-xl font-semibold text-gray-900">{completedResultCount}</p>
              </div>
            </div>
            <p className="text-xs text-gray-500">Ready now hands off into the post-interview recruiter workspace instead of ending the flow.</p>
          </div>

          <Button
            variant="outline"
            className="w-full justify-center"
            onClick={() => router.push(effectiveJobId ? `/results?jobId=${encodeURIComponent(effectiveJobId)}` : "/results")}
            disabled={orderedItems.length === 0}
          >
            Open Results Workspace
          </Button>

          <Button variant="outline" className="w-full justify-center" onClick={() => router.push("/review")}>
            Back to Review
          </Button>
        </CardContent>
      </Card>

      <Modal
        open={Boolean(activeCandidate)}
        onOpenChange={(open) => {
          if (!open) {
            setActiveCandidate(null);
            setActiveInsights(null);
          }
        }}
        title={activeCandidate ? `${resolveCandidateName(activeCandidate)} status` : "Candidate status"}
        description="Current ATS, outreach, and interview status for this candidate."
        className="max-w-4xl"
      >
        <div className="max-h-[78vh] space-y-5 overflow-y-auto pr-1">
          {workflowLoading && <p className="text-sm text-gray-600">Loading candidate status...</p>}
          {workflowError && <p className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{workflowError}</p>}
          {workflowMessage && <p className="rounded-xl border border-green-100 bg-green-50 px-4 py-3 text-sm text-green-700">{workflowMessage}</p>}

          <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-white/80 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Candidate status</p>
                <h3 className="mt-1 text-lg font-semibold text-gray-900">{formatStatus(activeCandidate?.status)}</h3>
                <p className="mt-1 text-sm text-gray-600">
                  {String(activeCandidateDetail?.ats_status_reason || "Current status tracked across outreach and interview progression.")}
                </p>
              </div>
              <Badge variant="neutral">{resolveCandidateName(activeCandidate)}</Badge>
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <div className="rounded-xl border border-[#ECE7DE] bg-[#F8F5EE] p-4 text-sm text-gray-700">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Profile</p>
                <div className="mt-2 space-y-1">
                  <p>Name: {resolveCandidateName(activeCandidate)}</p>
                  <p>Source: {activeCandidateDetail?.sourceProvider === "xray_apollo" ? "Candidate source" : String(activeCandidateDetail?.sourceProvider || activeCandidateDetail?.ats_status_source || "system")}</p>
                  <p>Updated: {String(activeCandidateDetail?.ats_status_updated_at || "n/a")}</p>
                </div>
              </div>

              <div className="rounded-xl border border-[#ECE7DE] bg-[#F8F5EE] p-4 text-sm text-gray-700">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Outreach</p>
                <div className="mt-2 space-y-1">
                  <p>Email: {String(activeCandidateDetail?.contactEmail || "pending")}</p>
                  <p>Phone: {String(activeCandidateDetail?.contactPhone || "pending")}</p>
                  <p>Enrichment: {String(activeCandidateDetail?.enrichmentStatus || "pending")}{activeCandidateDetail?.enrichmentSource ? ` via ${String(activeCandidateDetail.enrichmentSource)}` : ""}</p>
                </div>
              </div>
            </div>
          </div>

          {profileSummaryLines.length > 0 && (
            <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F8F5EE] p-4 text-sm text-gray-700">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Summary</p>
              <div className="mt-3 space-y-2">
                {profileSummaryLines.map((line, index) => (
                  <p key={`${activeCandidate?.candidateId}-summary-${index}`} className="rounded-xl bg-white/80 p-3">{line}</p>
                ))}
              </div>
            </div>
          )}

        </div>
      </Modal>
    </AppShell>
  );
}

export default function ReadyPage() {
  return (
    <Suspense fallback={<div className="mx-auto w-full max-w-5xl px-4 py-6 text-sm text-gray-600">Loading ready page...</div>}>
      <ReadyPageContent />
    </Suspense>
  );
}
