"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, Briefcase, MapPin, Star, VideoOff } from "lucide-react";

import { AppShell } from "@/components/layout/app-shell";
import { InterviewRecordingPlayer } from "@/components/results/interview-recording-player";
import { SecondRoundSchedulingModal, type SecondRoundInviteValues } from "@/components/results/second-round-scheduling-modal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAppContext } from "@/context/AppContext";
import {
  advanceResultWorkflow,
  getResultWorkspace,
  getResultsList,
  submitResultDecision,
  type ResultListItem,
  type ResultWorkspaceResponse,
} from "@/lib/api/results";

const RESULTS_RETENTION_DAYS = 7;

function toValidDate(value: unknown): Date | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function resolveResultsExpiry(workspace: ResultWorkspaceResponse, item: ResultListItem | null): Date | null {
  const explicitExpiry =
    toValidDate(workspace.expiresAt) ||
    toValidDate(item?.expiresAt) ||
    toValidDate(workspace.metadata.expiresAt) ||
    toValidDate(workspace.metadata.expires_at);
  if (explicitExpiry) return explicitExpiry;

  const createdAt =
    toValidDate(workspace.createdAt) ||
    toValidDate(item?.createdAt) ||
    toValidDate(workspace.metadata.createdAt) ||
    toValidDate(workspace.metadata.created_at);
  if (!createdAt) return null;

  return new Date(createdAt.getTime() + RESULTS_RETENTION_DAYS * 24 * 60 * 60 * 1000);
}

function emptyWorkspace(): ResultWorkspaceResponse {
  return {
    candidate: { id: "", name: "", role: "", company: "", headline: "", location: "", email: "", summary: "", skills: [], source: "" },
    recording: { sessionToken: "", recordingPath: "", videoAvailable: false },
    transcript: "",
    summary: "",
    scores: { overall: 0, technical: 0, communication: 0, cultureFit: 0 },
    decision: "",
    status: "",
    timeline: {},
    recommendation: "",
    analysis: { strengths: [], weaknesses: [], riskAreas: [], communication: "", technicalDepth: "" },
    metadata: {},
    operations: { decisionState: "", availableActions: ["pass", "advance", "hold", "reject"], followUpPrompt: { show: false, message: "" } },
  };
}

function labelForScore(score: number) {
  if (score >= 8.5) return "Strong Hire";
  if (score >= 7) return "Hire";
  if (score >= 5.5) return "Hold";
  return "Reject";
}

function formatScore(score: number) {
  return Number(score || 0).toFixed(1);
}

function scoreTone(score: number) {
  if (score >= 8.5) return "text-emerald-600";
  if (score >= 7) return "text-indigo-600";
  if (score >= 5.5) return "text-amber-600";
  return "text-rose-600";
}

function ResultRow({ label, score, description }: { label: string; score: number; description: string }) {
  const pct = Math.max(0, Math.min(100, Math.round((score / 10) * 100)));
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold text-slate-800">{label}</p>
        <p className={`text-sm font-bold ${scoreTone(score)}`}>{formatScore(score)} / 10</p>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div className="h-full rounded-full bg-gradient-to-r from-[#6d5efc] to-[#4f46e5]" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-sm leading-6 text-slate-500">{description}</p>
    </div>
  );
}

function ResultsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, isSessionReady, jobId, setJobId } = useAppContext();
  const queryJobId = String(searchParams.get("jobId") || "").trim();
  const effectiveJobId = jobId || queryJobId;

  const [items, setItems] = useState<ResultListItem[]>([]);
  const [selectedToken, setSelectedToken] = useState("");
  const [workspace, setWorkspace] = useState<ResultWorkspaceResponse>(emptyWorkspace());
  const [listLoading, setListLoading] = useState(true);
  const [wsLoading, setWsLoading] = useState(false);
  const [decisionLoadingToken, setDecisionLoadingToken] = useState("");
  const [selectedForSecondRound, setSelectedForSecondRound] = useState<ResultListItem | null>(null);
  const [inviteLoadingToken, setInviteLoadingToken] = useState("");

  const selectedItem = useMemo(
    () => items.find((item) => item.workflowToken === selectedToken) ?? items[0] ?? null,
    [items, selectedToken],
  );
  const resultsExpiry = useMemo(() => resolveResultsExpiry(workspace, selectedItem), [workspace, selectedItem]);
  const hasExpiredResults = Boolean(resultsExpiry && resultsExpiry.getTime() <= Date.now());
  const remainingDays = useMemo(() => {
    if (!resultsExpiry) return null;
    return Math.max(0, Math.ceil((resultsExpiry.getTime() - Date.now()) / (24 * 60 * 60 * 1000)));
  }, [resultsExpiry]);

  const loadList = async () => {
    if (!effectiveJobId || !user) return;
    setListLoading(true);
    const res = await getResultsList(effectiveJobId);
    if (res.success && res.data) {
      const candidates = res.data.candidates || [];
      setItems(candidates);
      setSelectedToken((current) => current || candidates[0]?.workflowToken || "");
    }
    setListLoading(false);
  };

  const loadWorkspace = async (token: string) => {
    if (!token) return;
    setWsLoading(true);
    const res = await getResultWorkspace(token);
    if (res.success && res.data) {
      setWorkspace(res.data);
    } else {
      setWorkspace(emptyWorkspace());
    }
    setWsLoading(false);
  };

  const handleDecision = async (workflowToken: string, decision: "pass" | "reject") => {
    if (decision === "pass") {
      const item = items.find((candidate) => candidate.workflowToken === workflowToken) ?? null;
      setSelectedToken(workflowToken);
      setSelectedForSecondRound(item);
      return;
    }
    setDecisionLoadingToken(workflowToken);
    const res = await submitResultDecision(workflowToken, { decision });
    if (res.success && res.data && workflowToken === selectedToken) {
      void loadWorkspace(workflowToken);
    }
    setDecisionLoadingToken("");
  };

  const handleSecondRoundSubmit = async (values: SecondRoundInviteValues) => {
    if (!selectedForSecondRound || inviteLoadingToken) return;
    setInviteLoadingToken(selectedForSecondRound.workflowToken);
    const payload = {
      roundType: values.roundType,
      mode: values.mode,
      meetUrl: values.meetUrl,
      officeAddress: values.officeAddress,
      interviewer: {
        name: values.interviewerName,
        email: values.interviewerEmail,
      },
      recruiterEmail: values.recruiterEmail,
      slots: [values.interviewDate, values.interviewTime].filter(Boolean),
      notes: values.additionalNotes,
      timezone: values.timezone,
      duration: "",
      panelInterviewers: [],
    };
    const res = await advanceResultWorkflow(selectedForSecondRound.workflowToken, payload);
    if (res.success) {
      setSelectedForSecondRound(null);
    }
    setInviteLoadingToken("");
  };

  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    if (!effectiveJobId) {
      router.replace("/job");
      return;
    }
    void loadList();
  }, [effectiveJobId, isSessionReady, router, user]);

  useEffect(() => {
    if (jobId || !queryJobId) return;
    setJobId(queryJobId);
  }, [jobId, queryJobId, setJobId]);

  useEffect(() => {
    if (!selectedToken) return;
    void loadWorkspace(selectedToken);
  }, [selectedToken]);

  const scoreLabel = labelForScore(workspace.scores.overall);
  const scoreRows = [
    { label: "Communication", score: workspace.scores.communication, description: workspace.analysis.communication || "Clear, structured, and confident responses." },
    { label: "Technical Skills", score: workspace.scores.technical, description: workspace.analysis.technicalDepth || "Shows practical problem-solving depth." },
    { label: "Culture Fit", score: workspace.scores.cultureFit, description: workspace.analysis.riskAreas?.length ? workspace.analysis.riskAreas.join(" ") : "Aligned with team expectations and collaboration." },
    { label: "Overall", score: workspace.scores.overall, description: workspace.summary || "A well-rounded interview summary appears here." },
  ];

  return (
    <AppShell activeStep={5}>
      <div className="mx-auto w-full max-w-[1600px] px-4 py-6">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <Button variant="outline" className="rounded-full" onClick={() => router.push("/workspace")}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back to workspace
          </Button>
          <p className="text-sm font-bold uppercase tracking-[0.18em] text-indigo-600">Interview Analysis</p>
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.75fr)_minmax(320px,1fr)]">
          <div className="space-y-4">
            <section className="rounded-[28px] border border-[rgba(120,100,80,0.08)] bg-white p-5 shadow-[0_16px_40px_rgba(0,0,0,0.04)]">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="space-y-3">
                  <h1 className="text-3xl font-semibold tracking-tight text-slate-900">
                    {selectedItem?.name || workspace.candidate.name || "Candidate"}
                  </h1>
                  <div className="flex flex-wrap items-center gap-3 text-sm text-slate-600">
                    <span className="inline-flex items-center gap-2 rounded-full bg-indigo-50 px-3 py-1 font-medium text-indigo-700">
                      <Briefcase className="h-4 w-4" />
                      {workspace.candidate.role || selectedItem?.recommendation || "Interview result"}
                    </span>
                    <span className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1">
                      <MapPin className="h-4 w-4" />
                      {workspace.candidate.location || "Location not set"}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-5 text-sm font-medium text-slate-600">
                    <span>Interview ID: {selectedItem?.workflowToken || workspace.recording.sessionToken || "—"}</span>
                    <span>Duration: {String((workspace.metadata as Record<string, unknown>).duration || "48 min")}</span>
                  </div>
                </div>

                <div className="min-w-[240px] rounded-2xl border border-emerald-100 bg-emerald-50 px-5 py-4">
                  <div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.12em] text-slate-500">
                    <Star className="h-4 w-4 text-indigo-600" />
                    Overall score
                  </div>
                  <div className="mt-2 flex items-end justify-between gap-4">
                    <div className={`text-5xl font-semibold tracking-tight ${scoreTone(workspace.scores.overall)}`}>
                      {formatScore(workspace.scores.overall)}
                      <span className="text-xl text-slate-500">/10</span>
                    </div>
                    <Badge variant="high">{scoreLabel}</Badge>
                  </div>
                  <p className="mt-3 text-sm leading-6 text-slate-600">
                    Final recommendation and score remain visible beside the candidate summary.
                  </p>
                </div>
              </div>
            </section>

            {hasExpiredResults ? (
              <section className="rounded-[28px] border border-amber-100 bg-amber-50 p-5 shadow-[0_16px_40px_rgba(0,0,0,0.04)]">
                <div className="flex items-center gap-3">
                  <div className="rounded-full bg-amber-100 p-2 text-amber-700">
                    <VideoOff className="h-5 w-5" />
                  </div>
                  <div>
                    <h2 className="text-2xl font-semibold text-amber-950">Results expired from the UI</h2>
                    <p className="mt-1 text-sm text-amber-800">
                      Video playback and AI analysis are hidden after 7 days, but the underlying records remain stored in the database.
                    </p>
                  </div>
                </div>
                <div className="mt-4 rounded-2xl border border-amber-200 bg-white/70 p-4 text-sm text-amber-900">
                  <p className="font-medium">Retention status</p>
                  <p className="mt-1">
                    {resultsExpiry
                      ? `This result expired on ${resultsExpiry.toLocaleDateString()}.`
                      : "No expiry timestamp was found, so the UI is treating this result as expired."}
                  </p>
                </div>
              </section>
            ) : (
              <>
                <InterviewRecordingPlayer
                  workflowToken={selectedToken}
                  available={Boolean(workspace.recording.videoAvailable || workspace.recording.recordingPath)}
                  title="Interview recording"
                  className="rounded-[28px]"
                />

                <section className="rounded-[28px] border border-[rgba(120,100,80,0.08)] bg-white p-5 shadow-[0_16px_40px_rgba(0,0,0,0.04)]">
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <div>
                      <h2 className="text-2xl font-semibold text-slate-900">AI Interview Analysis</h2>
                      <p className="mt-1 text-sm text-slate-500">A concise summary of communication, technical depth, and overall interview takeaways.</p>
                    </div>
                    <Button variant="outline" className="rounded-full">Read more</Button>
                  </div>
                  <div className="space-y-4">
                    <p className="text-[15px] leading-7 text-slate-700">
                      {workspace.summary || "AI summary will appear here once the interview evaluation is complete."}
                    </p>
                    <div className="grid gap-3 md:grid-cols-2">
                      <div className="rounded-2xl bg-slate-50 p-4">
                        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Score justification</p>
                        <p className="mt-2 text-sm leading-6 text-slate-700">
                          {workspace.decision || "Score justification will appear here from the interview analysis data."}
                        </p>
                      </div>
                      <div className="rounded-2xl bg-slate-50 p-4">
                        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Transcript preview</p>
                        <p className="mt-2 text-sm leading-6 text-slate-700">
                          {workspace.transcript ? workspace.transcript.slice(0, 320) : "Transcript not available yet."}
                        </p>
                      </div>
                    </div>
                  </div>
                </section>
              </>
            )}
          </div>

          <div className="space-y-4">
            <section className="rounded-[28px] border border-[rgba(120,100,80,0.08)] bg-white p-5 shadow-[0_16px_40px_rgba(0,0,0,0.04)]">
              <h2 className="text-2xl font-semibold text-slate-900">Scoring Parameters</h2>
              <p className="mt-1 text-sm text-slate-500">
                High-level scoring summary using the existing backend values.
                {hasExpiredResults ? " Hidden from the main UI after the 7-day retention window." : ""}
              </p>
              <div className="mt-5 space-y-5">
                {scoreRows.map((row) => <ResultRow key={row.label} {...row} />)}
              </div>
            </section>

            <section className="rounded-[28px] border border-[rgba(120,100,80,0.08)] bg-white p-5 shadow-[0_16px_40px_rgba(0,0,0,0.04)]">
              <h2 className="text-2xl font-semibold text-slate-900">Transcript</h2>
              <div className="mt-4 max-h-[420px] space-y-3 overflow-auto pr-1">
                {hasExpiredResults ? (
                  <div className="flex h-40 items-center justify-center rounded-2xl border border-dashed border-amber-200 bg-amber-50 text-sm text-amber-800">
                    Transcript hidden after the 7-day UI retention window.
                  </div>
                ) : workspace.transcript ? (
                  workspace.transcript.split("\n").filter(Boolean).map((line, index) => (
                    <div key={index} className="rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-700">
                      {line}
                    </div>
                  ))
                ) : (
                  <div className="flex h-40 items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-500">
                    Transcript not available yet.
                  </div>
                )}
              </div>
            </section>
          </div>
        </div>

        <div className="mt-4 rounded-[28px] border border-[rgba(120,100,80,0.08)] bg-white p-4 shadow-[0_16px_40px_rgba(0,0,0,0.04)]">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.12em] text-indigo-600">Interview queue</p>
              <p className="text-sm text-slate-500">Select another completed interview to review its recording and scores.</p>
              <p className="text-xs text-slate-500">
                {remainingDays !== null && !hasExpiredResults
                  ? `This result stays visible for about ${remainingDays} more day${remainingDays === 1 ? "" : "s"}.`
                  : "Expired results are hidden from the UI but remain stored in the database."}
              </p>
            </div>
            <Badge variant="neutral">{items.length} candidates</Badge>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {listLoading ? (
              Array.from({ length: 3 }).map((_, index) => (
                <div key={index} className="h-24 animate-pulse rounded-2xl bg-slate-100" />
              ))
            ) : (
              items.map((item) => (
                <div
                  key={item.workflowToken}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedToken(item.workflowToken)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedToken(item.workflowToken);
                    }
                  }}
                  className={[
                    "rounded-2xl border p-4 text-left transition",
                    item.workflowToken === selectedToken ? "border-indigo-300 bg-indigo-50" : "border-slate-200 bg-white hover:bg-slate-50",
                  ].join(" ")}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-semibold text-slate-900">{item.name}</p>
                      <p className="mt-1 text-xs text-slate-500">{item.recommendation || "Interview completed"}</p>
                    </div>
                    <Badge variant={item.completionState === "results_ready" ? "high" : "neutral"}>{item.completionState || item.status}</Badge>
                  </div>
                  <div className="mt-4 flex gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      className="flex-1 rounded-full border-emerald-200 text-emerald-700 hover:bg-emerald-50"
                      onClick={(event) => {
                        event.stopPropagation();
                        void handleDecision(item.workflowToken, "pass");
                      }}
                      disabled={Boolean(decisionLoadingToken) || Boolean(inviteLoadingToken)}
                    >
                      {selectedForSecondRound?.workflowToken === item.workflowToken && inviteLoadingToken === item.workflowToken ? "Opening..." : "Select"}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      className="flex-1 rounded-full border-rose-200 text-rose-700 hover:bg-rose-50"
                      onClick={(event) => {
                        event.stopPropagation();
                        void handleDecision(item.workflowToken, "reject");
                      }}
                      disabled={Boolean(decisionLoadingToken) || Boolean(inviteLoadingToken)}
                    >
                      {decisionLoadingToken === item.workflowToken ? "Saving..." : "Reject"}
                    </Button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        <SecondRoundSchedulingModal
          open={Boolean(selectedForSecondRound)}
          onOpenChange={(open) => {
            if (!open) setSelectedForSecondRound(null);
          }}
          candidateName={selectedForSecondRound?.name || ""}
          role={workspace.candidate.role || selectedForSecondRound?.recommendation || ""}
          company={workspace.candidate.company || ""}
          defaultRecruiterEmail={user?.email || ""}
          submitting={Boolean(inviteLoadingToken)}
          onSubmit={(values) => void handleSecondRoundSubmit(values)}
        />
      </div>
    </AppShell>
  );
}

export default function ResultsPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-gray-600">Loading results...</div>}>
      <ResultsPageContent />
    </Suspense>
  );
}
