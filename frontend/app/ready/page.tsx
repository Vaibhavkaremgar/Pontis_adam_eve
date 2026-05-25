"use client";

/**
 * What this file does:
 * Tracks contacted candidates and exposes the UI-side hiring workflow from the PDF.
 *
 * What API it connects to:
 * GET /interviews?jobId=...
 * POST /candidates/export
 * GET /interview/insights
 * POST /interview/evaluations
 * POST /interview/decision
 */
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Textarea } from "@/components/ui/textarea";
import { useAppContext } from "@/context/AppContext";
import { exportCandidates } from "@/lib/api/candidates";
import {
  getInterviewInsights,
  getInterviewStatuses,
  recordInterviewEvaluation,
  submitInterviewDecision,
  type InterviewInsights,
} from "@/lib/api/interviews";
import { getMetrics } from "@/lib/api/metrics";
import { getOutreachStatuses, type OutreachStatusItem } from "@/lib/api/outreach";
import type { InterviewStatus } from "@/types";

const STATUS_LABELS: Record<string, string> = {
  shortlisted: "Shortlisted",
  contacted: "Contacted",
  interview_scheduled: "Interview Scheduled",
  interview_invited: "Interview Invited",
  interview_completed: "Interview Completed",
  interview_no_show: "No-show",
  no_show: "No-show",
  booked: "Booked",
  rejected: "Rejected",
  exported: "Exported",
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

function statusVariant(status: InterviewStatus["status"]) {
  if (["booked", "interview_scheduled", "advanced", "final_round", "hired"].includes(status)) return "high";
  if (["interview_invited", "offer_sent"].includes(status)) return "info";
  if (["contacted", "shortlisted", "interview_completed"].includes(status)) return "medium";
  if (["rejected", "archived", "interview_no_show", "no_show"].includes(status)) return "low";
  return "neutral";
}

export default function ReadyPage() {
  const router = useRouter();
  const { user, isSessionReady, jobId } = useAppContext();
  const [items, setItems] = useState<InterviewStatus[]>([]);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const [isExporting, setIsExporting] = useState(false);
  const [outreachStatuses, setOutreachStatuses] = useState<OutreachStatusItem[]>([]);
  const [metrics, setMetrics] = useState<{
    emails_sent: number;
    replies_received: number;
    interviews_booked: number;
    conversion_rate: number;
  } | null>(null);

  const [activeCandidate, setActiveCandidate] = useState<InterviewStatus | null>(null);
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

  const orderedItems = useMemo(() => {
    const priority: Record<string, number> = {
      contacted: 0,
      interview_invited: 1,
      booked: 2,
      interview_scheduled: 3,
      advanced: 4,
      final_round: 5,
      offer_sent: 6,
      hired: 7,
      shortlisted: 8,
      rejected: 9,
      archived: 10,
    };

    return [...items].sort((a, b) => {
      const aRank = priority[a.status] ?? 20;
      const bRank = priority[b.status] ?? 20;
      if (aRank !== bRank) return aRank - bRank;
      return a.name.localeCompare(b.name) || a.candidateId.localeCompare(b.candidateId);
    });
  }, [items]);

  const itemByCandidateId = useMemo(() => {
    return new Map(orderedItems.map((item) => [item.candidateId, item]));
  }, [orderedItems]);

  const loadReady = async () => {
    if (!jobId || !user) return;
    const canViewOperationalMetrics = user.role === "admin" || user.role === "internal_ops";
    setIsLoading(true);
    setError("");
    const [result, outreachResult] = await Promise.all([getInterviewStatuses(jobId), getOutreachStatuses(jobId)]);
    const metricsResult = canViewOperationalMetrics ? await getMetrics() : null;
    if (!result.success || !result.data) {
      setError(result.error || "Could not load interview statuses.");
    } else {
      setItems(result.data);
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
    if (!jobId) {
      router.replace("/job");
      return;
    }
    void loadReady();
  }, [isSessionReady, jobId, router, user]);

  const refreshWorkflow = async (candidate: InterviewStatus) => {
    if (!jobId) return;
    setWorkflowLoading(true);
    setWorkflowError("");
    const result = await getInterviewInsights(jobId, candidate.candidateId);
    if (!result.success || !result.data) {
      setWorkflowError(result.error || "Could not load hiring workflow details.");
      setActiveInsights(null);
    } else {
      setActiveInsights(result.data);
    }
    setWorkflowLoading(false);
  };

  const openWorkflow = (candidate: InterviewStatus) => {
    setActiveCandidate(candidate);
    setWorkflowMessage("");
    setWorkflowError("");
    setEvaluationSummary("");
    setDecisionNote("");
    setInviteNote("");
    void refreshWorkflow(candidate);
  };

  const handleExport = async () => {
    if (!jobId || isExporting) return;
    setIsExporting(true);
    setExportMessage("");
    const candidateIds = orderedItems.filter((item) => !["rejected", "archived"].includes(item.status)).map((item) => item.candidateId);
    const result = await exportCandidates({ jobId, candidateIds });
    if (!result.success || !result.data) {
      setExportMessage(result.error || "Failed to export candidates.");
      setIsExporting(false);
      return;
    }
    setExportMessage(`Export ${result.data.status}: ${result.data.exportedCount} candidate${result.data.exportedCount !== 1 ? "s" : ""} (ref: ${result.data.reference})`);
    setIsExporting(false);
  };

  const runDecision = async (action: string, targetStage = "") => {
    if (!jobId || !activeCandidate || workflowLoading) return;
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
      jobId,
      candidateId: activeCandidate.candidateId,
      action,
      targetStage,
      notes: schedulingNote,
      interviewerId: interviewer,
      recommendation: action,
      sourceType: "adam",
    });
    if (!result.success) {
      setWorkflowError(result.error || "Could not update the hiring workflow.");
    } else {
      setWorkflowMessage("Hiring workflow updated.");
      await refreshWorkflow(activeCandidate);
      await loadReady();
    }
    setWorkflowLoading(false);
  };

  const markPreScreenComplete = async () => {
    if (!jobId || !activeCandidate || workflowLoading) return;
    setWorkflowLoading(true);
    setWorkflowMessage("");
    setWorkflowError("");
    const stageName = String(activeInsights?.currentStage || "recruiter_screen");
    const result = await recordInterviewEvaluation({
      jobId,
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
      String(latestEvaluation?.summary || intelligence.summary || payload.summary || "Candidate has moved into the post-outreach hiring workflow."),
      String(intelligence.recommendationSignal || payload.recommendation || "Adam is tracking stage progression, reply context, and recruiter decisions."),
      String(payload.resumeText || payload.resume_text || "Resume/video profile appears here once candidate reply and interview recorder data are available."),
    ].filter(Boolean).slice(0, 3);
  }, [activeInsights]);

  return (
    <AppShell activeStep={5}>
      <Card className="mx-auto w-full max-w-[760px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Candidates ready for interview</CardTitle>
          <CardDescription>Adam tracks selected candidates here and continues the PDF hiring flow after resume replies</CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {isLoading && <p className="text-sm text-gray-600">Loading interview statuses...</p>}

          {!isLoading && !error && items.length === 0 && (
            <div className="rounded-xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4 text-sm text-gray-600">
              No replies yet. Adam is still processing selections or no candidate has booked an interview.
            </div>
          )}

          {orderedItems.map((item) => (
            <div key={item.candidateId} className="space-y-3 rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] p-4">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="font-semibold text-gray-900">{item.name || item.candidateId.slice(0, 8)}</p>
                  <p className="text-xs text-gray-500">{item.candidateId.slice(0, 12)}...</p>
                </div>
                <Badge variant={statusVariant(item.status)}>{formatStatus(item.status)}</Badge>
              </div>
              <Button className="w-full justify-center" variant="outline" disabled={isLoading} onClick={() => openWorkflow(item)}>
                Open Hiring Workflow
              </Button>
            </div>
          ))}

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
                      <p className="text-sm font-medium text-gray-900">{readyItem?.name || item.candidateId.slice(0, 8)}</p>
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
                <p className="text-gray-500">Interviews booked</p>
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
        title={activeCandidate ? `${activeCandidate.name || activeCandidate.candidateId.slice(0, 8)} hiring workflow` : "Hiring workflow"}
        description="PDF steps 7-15: profile, interview outcome, second round, offer, and placement."
        className="max-w-4xl"
      >
        <div className="max-h-[78vh] space-y-5 overflow-y-auto pr-1">
          {workflowLoading && <p className="text-sm text-gray-600">Updating workflow...</p>}
          {workflowError && <p className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{workflowError}</p>}
          {workflowMessage && <p className="rounded-xl border border-green-100 bg-green-50 px-4 py-3 text-sm text-green-700">{workflowMessage}</p>}

          <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-white/70 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Full profile card</p>
                <h3 className="mt-1 text-lg font-semibold text-gray-900">{formatStatus(activeCandidate?.status)}</h3>
              </div>
              <Badge variant="neutral">Video pending integration</Badge>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-[0.8fr_1.2fr]">
              <div className="rounded-xl border border-dashed border-[#D8CDBD] bg-[#F8F5EE] p-4 text-sm text-gray-600">
                <p className="font-semibold text-gray-900">Video interview</p>
                <p className="mt-2">Recorder processing placeholder. Once wired, Adam can attach video, transcript, and score overlays here.</p>
              </div>
              <div className="space-y-2 text-sm text-gray-700">
                {profileSummaryLines.map((line, index) => (
                  <p key={`${activeCandidate?.candidateId}-summary-${index}`} className="rounded-xl bg-[#F8F5EE] p-3">{line}</p>
                ))}
              </div>
            </div>
          </div>

          <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Pre-screening interview completed</p>
            <Textarea
              className="mt-3"
              value={evaluationSummary}
              onChange={(event) => setEvaluationSummary(event.target.value)}
              placeholder="Paste Adam's 3-paragraph summary, score notes, resume observations, or video notes."
            />
            <Button className="mt-3 w-full justify-center" onClick={() => void markPreScreenComplete()} disabled={workflowLoading}>
              Mark Pre-screen Complete
            </Button>
          </div>

          <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-white/70 p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Recruiter swipes on interview</p>
            <Textarea className="mt-3 min-h-[86px]" value={decisionNote} onChange={(event) => setDecisionNote(event.target.value)} placeholder="Decision note or reason." />
            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              <Button variant="outline" className="justify-center text-red-700 hover:bg-red-50" onClick={() => void runDecision("reject")} disabled={workflowLoading}>
                Pass / Disqualify
              </Button>
              <Button className="justify-center" onClick={() => void runDecision("advance", "technical_round")} disabled={workflowLoading}>
                Like / 2nd Round
              </Button>
              <Button variant="outline" className="justify-center" onClick={() => void runDecision("no_show")} disabled={workflowLoading}>
                No-show
              </Button>
            </div>
          </div>

          <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Second-round scheduling modal</p>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <Input value={scheduleMode} onChange={(event) => setScheduleMode(event.target.value)} placeholder="Mode, e.g. video / onsite" />
              <Input value={interviewer} onChange={(event) => setInterviewer(event.target.value)} placeholder="Interviewer email or ID" />
              <Input type="date" value={slotDate} onChange={(event) => setSlotDate(event.target.value)} />
              <Input value={slotOptions} onChange={(event) => setSlotOptions(event.target.value)} placeholder="Slots, e.g. 10am, 2pm, 4pm" />
            </div>
            <Textarea className="mt-3 min-h-[86px]" value={inviteNote} onChange={(event) => setInviteNote(event.target.value)} placeholder="Note to candidate, recruiter, and interviewer." />
            <Button className="mt-3 w-full justify-center" onClick={() => void runDecision("advance", "technical_round")} disabled={workflowLoading}>
              Trigger 2nd Round Invite Draft
            </Button>
          </div>

          <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-white/70 p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Outcome and offer</p>
            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              <Button variant="outline" className="justify-center" onClick={() => void runDecision("archive")} disabled={workflowLoading}>
                Not Moving Forward
              </Button>
              <Button variant="outline" className="justify-center" onClick={() => void runDecision("mark_offer")} disabled={workflowLoading}>
                Offer Made
              </Button>
              <Button className="justify-center" onClick={() => void runDecision("mark_placed")} disabled={workflowLoading}>
                Offer Accepted / Placed
              </Button>
            </div>
          </div>

          <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F8F5EE] p-4 text-sm text-gray-700">
            <p className="font-semibold text-gray-900">Workflow state</p>
            <p className="mt-2">Current stage: {activeInsights?.currentStage || "Not started"}</p>
            <p>Workflow token: {(activeInsights?.workflowToken || "").slice(0, 16) || "pending"}</p>
            <p>Evaluations: {activeInsights?.evaluationCount ?? 0}</p>
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
