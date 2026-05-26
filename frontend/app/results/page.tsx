"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  BarChart3,
  Clock3,
  FileText,
  ListOrdered,
  PlayCircle,
  RectangleEllipsis,
  ShieldCheck,
  Sparkles,
  UserCircle2,
  Video,
} from "lucide-react";

import { Navbar } from "@/components/layout/navbar";
import { Stepper } from "@/components/layout/stepper";
import { ResultsPipelineNav } from "@/components/results/results-pipeline-nav";
import { InterviewRecordingPlayer } from "@/components/results/interview-recording-player";
import { SecondRoundSchedulingModal, type SecondRoundSchedulingValues } from "@/components/results/second-round-scheduling-modal";
import { parseTranscriptSegments, type TranscriptSegment } from "@/components/results/transcript-utils";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useAppContext } from "@/context/AppContext";
import { logEvent } from "@/lib/logger";
import {
  advanceResultWorkflow,
  getResultWorkspace,
  getResultsList,
  submitResultDecision,
  type ResultListItem,
  type ResultWorkspaceResponse,
} from "@/lib/api/results";

type TabKey = "video" | "analysis" | "transcript" | "resume" | "timeline";

const TABS: Array<{ key: TabKey; label: string; icon: typeof Video }> = [
  { key: "video", label: "Video", icon: Video },
  { key: "analysis", label: "AI Analysis", icon: Sparkles },
  { key: "transcript", label: "Transcript", icon: FileText },
  { key: "resume", label: "Resume", icon: UserCircle2 },
  { key: "timeline", label: "Timeline", icon: ListOrdered },
];

const RESULT_LABELS: Record<string, string> = {
  interview_completed: "Interview Completed",
  evaluation_processing: "Evaluation Processing",
  results_ready: "Results Ready",
  advanced: "Advanced",
  second_round_requested: "Second Round Requested",
  second_round_scheduled: "Second Round Scheduled",
  final_round: "Final Round",
  offer_stage: "Offer Stage",
  offer_sent: "Offer Sent",
  placed: "Placed",
  search_closed: "Search Closed",
  interview_in_progress: "Interview In Progress",
  interview_scheduled: "Interview Scheduled",
  rejected: "Rejected",
};

function formatStatus(status: string) {
  const normalized = String(status || "").trim().toLowerCase();
  return RESULT_LABELS[normalized] || normalized.replace(/_/g, " ") || "Unknown";
}

function scoreTone(score: number) {
  if (score >= 85) return "high";
  if (score >= 70) return "medium";
  if (score >= 50) return "info";
  return "low";
}

function truncate(value: string, limit = 160) {
  const text = String(value || "").trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, limit).trim()}...`;
}

function segmentSpeakerTone(segment: TranscriptSegment) {
  if (segment.role === "candidate") return "border-sky-200 bg-sky-50 text-slate-900";
  if (segment.role === "interviewer") return "border-emerald-200 bg-emerald-50 text-slate-900";
  return "border-slate-200 bg-slate-50 text-slate-700";
}

function toTimelineRows(value: ResultWorkspaceResponse["timeline"]): Array<Record<string, unknown>> {
  if (Array.isArray(value?.events)) {
    return value.events.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"));
  }
  return Object.entries(value || {}).map(([key, entry]) => ({
    type: key,
    value: entry,
  }));
}

function emptyWorkspace(): ResultWorkspaceResponse {
  return {
    candidate: {
      id: "",
      name: "",
      role: "",
      company: "",
      headline: "",
      location: "",
      email: "",
      summary: "",
      skills: [],
      source: "",
    },
    recording: {
      sessionToken: "",
      recordingPath: "",
      videoAvailable: false,
    },
    transcript: "",
    summary: "",
    scores: {
      overall: 0,
      technical: 0,
      communication: 0,
      cultureFit: 0,
    },
    decision: "",
    status: "",
    timeline: {},
    recommendation: "",
    analysis: {
      strengths: [],
      weaknesses: [],
      riskAreas: [],
      communication: "",
      technicalDepth: "",
    },
    metadata: {},
    operations: {
      decisionState: "",
      availableActions: ["pass", "advance", "hold", "reject"],
      followUpPrompt: {
        show: false,
        message: "Would you like to advance this candidate?",
      },
    },
  };
}

function ResultsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, isSessionReady, jobId, setJobId } = useAppContext();
  const [items, setItems] = useState<ResultListItem[]>([]);
  const [selectedWorkflowToken, setSelectedWorkflowToken] = useState("");
  const [workspace, setWorkspace] = useState<ResultWorkspaceResponse>(emptyWorkspace());
  const [activeTab, setActiveTab] = useState<TabKey>("video");
  const [listLoading, setListLoading] = useState(true);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [listError, setListError] = useState("");
  const [workspaceError, setWorkspaceError] = useState("");
  const [actionLoading, setActionLoading] = useState<"pass" | "advance" | "hold" | "reject" | "">("");
  const [advanceModalOpen, setAdvanceModalOpen] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const queryJobId = String(searchParams.get("jobId") || "").trim();
  const effectiveJobId = jobId || queryJobId;

  const selectedItem = useMemo(() => items.find((item) => item.workflowToken === selectedWorkflowToken) || items[0] || null, [items, selectedWorkflowToken]);
  const transcriptSegments = useMemo(() => parseTranscriptSegments(workspace.transcript), [workspace.transcript]);
  const timelineRows = useMemo(() => toTimelineRows(workspace.timeline), [workspace.timeline]);
  const candidateCount = items.length;
  const recruiterEmail = String((workspace.metadata?.recruiterEmail as string) || user?.email || "").trim();
  const followUpPrompt = workspace.operations?.followUpPrompt?.show ? workspace.operations.followUpPrompt.message || "Would you like to advance this candidate?" : "";
  const decisionState = String(workspace.operations?.decisionState || "").trim();

  const loadResults = async () => {
    if (!effectiveJobId || !user) return;
    setListLoading(true);
    setListError("");
    const result = await getResultsList(effectiveJobId);
    if (!result.success || !result.data) {
      setListError(result.error || "Could not load interview results.");
      setItems([]);
      setSelectedWorkflowToken("");
      setWorkspace(emptyWorkspace());
      setListLoading(false);
      return;
    }

    setItems(result.data.candidates);
    logEvent({
      event: "results_list_rendered",
      payload: {
        workflow_token: "",
        candidate_id: "",
        recruiter_id: result.data.recruiterId || user.id,
        job_id: effectiveJobId,
        candidate_count: result.data.candidates.length,
      },
    });
    if (result.data.candidates.length === 0) {
      setSelectedWorkflowToken("");
      setWorkspace(emptyWorkspace());
      setListLoading(false);
      return;
    }
    const firstToken = result.data.candidates[0]?.workflowToken || "";
    setSelectedWorkflowToken((current) => current || firstToken);
    setListLoading(false);
  };

  const loadWorkspace = async (workflowToken: string) => {
    if (!workflowToken) return;
    setWorkspaceLoading(true);
    setWorkspaceError("");
    const result = await getResultWorkspace(workflowToken);
    if (!result.success || !result.data) {
      setWorkspaceError(result.error || "Could not load result workspace.");
      setWorkspace(emptyWorkspace());
      setWorkspaceLoading(false);
      return;
    }

    setWorkspace(result.data);
    logEvent({
      event: "results_workspace_rendered",
      payload: {
        workflow_token: workflowToken,
        candidate_id: result.data.metadata?.candidateId || result.data.candidate.id || "",
        recruiter_id: result.data.metadata?.recruiterId || user?.id || "",
        job_id: result.data.metadata?.jobId || effectiveJobId || "",
        status: result.data.status,
      },
    });
    setWorkspaceLoading(false);
  };

  const refreshCurrentWorkspace = async (workflowToken = selectedWorkflowToken) => {
    await loadResults();
    if (workflowToken) {
      await loadWorkspace(workflowToken);
    }
  };

  const handleDecision = async (decision: "pass" | "hold" | "reject") => {
    if (!selectedWorkflowToken) return;
    setActionLoading(decision);
    setActionMessage("");
    logEvent({
      event: "recruiter_decision_clicked",
      payload: {
        workflow_token: selectedWorkflowToken,
        candidate_id: workspace.metadata?.candidateId || workspace.candidate.id || "",
        recruiter_id: workspace.metadata?.recruiterId || user?.id || "",
        decision,
      },
    });
    const result = await submitResultDecision(selectedWorkflowToken, { decision });
    if (!result.success) {
      setActionMessage(result.error || "Could not record recruiter decision right now.");
      setActionLoading("");
      return;
    }
    setActionMessage(result.data?.duplicate ? "Decision was already recorded." : decision === "reject" ? "Candidate rejected and ATS updated." : `Decision recorded: ${decision}.`);
    await refreshCurrentWorkspace();
    setActionLoading("");
  };

  const handleAdvanceSubmit = async (values: SecondRoundSchedulingValues) => {
    if (!selectedWorkflowToken) return;
    setActionLoading("advance");
    setActionMessage("");
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
      slots: values.slots,
      notes: values.notes,
      timezone: values.timezone,
      duration: values.duration,
      panelInterviewers: values.panelInterviewers,
    };
    logEvent({
      event: "recruiter_advance_submitted",
      payload: {
        workflow_token: selectedWorkflowToken,
        candidate_id: workspace.metadata?.candidateId || workspace.candidate.id || "",
        recruiter_id: workspace.metadata?.recruiterId || user?.id || "",
        mode: values.mode,
        round_type: values.roundType,
      },
    });
    const result = await advanceResultWorkflow(selectedWorkflowToken, payload);
    if (!result.success) {
      setActionMessage(result.error || "Could not send second-round invite.");
      setActionLoading("");
      return;
    }
    setAdvanceModalOpen(false);
    setActionMessage(result.data?.duplicate ? "Second-round invite already exists." : "Second-round invite sent.");
    logEvent({
      event: "recruiter_advance_completed",
      payload: {
        workflow_token: selectedWorkflowToken,
        candidate_id: workspace.metadata?.candidateId || workspace.candidate.id || "",
        recruiter_id: workspace.metadata?.recruiterId || user?.id || "",
        invite_status: result.data?.status || "second_round_requested",
      },
    });
    await refreshCurrentWorkspace();
    setActionLoading("");
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
    setSelectedWorkflowToken("");
    setWorkspace(emptyWorkspace());
    void loadResults();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveJobId, isSessionReady, router, user]);

  useEffect(() => {
    if (jobId || !queryJobId) return;
    setJobId(queryJobId);
  }, [jobId, queryJobId, setJobId]);

  useEffect(() => {
    if (!selectedWorkflowToken) return;
    setActionMessage("");
    setActionLoading("");
    void loadWorkspace(selectedWorkflowToken);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedWorkflowToken]);

  const activeCandidate = selectedItem || null;
  const strengths = (workspace.analysis.strengths || []).filter(Boolean);
  const weaknesses = (workspace.analysis.weaknesses || []).filter(Boolean);
  const riskAreas = (workspace.analysis.riskAreas || []).filter(Boolean);

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(56,189,248,0.14),_transparent_35%),linear-gradient(180deg,_#f8fafc_0%,_#eef2ff_100%)] text-slate-900">
      <Navbar />
      <Stepper activeStep={6} />
      <ResultsPipelineNav active="Results" />

      <main className="mx-auto w-full max-w-[1600px] px-4 py-6 pb-28 sm:px-6 lg:px-8">
        <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <p className="text-xs font-semibold uppercase tracking-[0.28em] text-sky-700">Recruiter intelligence workspace</p>
            <h1 className="mt-2 font-heading text-3xl font-semibold tracking-[-0.03em] text-slate-950 sm:text-4xl">
              Interview results, intelligence, and playback in one place
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">
              Adam keeps the recruiter inside the workspace while Pontis remains the interview engine and source of truth for transcript, evaluation, and video.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Card className="border-slate-200/80 bg-white/85 shadow-sm">
              <CardContent className="p-4">
                <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Candidates</p>
                <p className="mt-2 text-2xl font-semibold text-slate-950">{candidateCount}</p>
              </CardContent>
            </Card>
            <Card className="border-slate-200/80 bg-white/85 shadow-sm">
              <CardContent className="p-4">
                <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Completed</p>
                <p className="mt-2 text-2xl font-semibold text-slate-950">{items.filter((item) => item.completionState === "results_ready").length}</p>
              </CardContent>
            </Card>
            <Card className="border-slate-200/80 bg-white/85 shadow-sm">
              <CardContent className="p-4">
                <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Video ready</p>
                <p className="mt-2 text-2xl font-semibold text-slate-950">{items.filter((item) => item.videoAvailable).length}</p>
              </CardContent>
            </Card>
            <Card className="border-slate-200/80 bg-white/85 shadow-sm">
              <CardContent className="p-4">
                <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Top score</p>
                <p className="mt-2 text-2xl font-semibold text-slate-950">{items[0] ? Math.round(items[0].score) : 0}</p>
              </CardContent>
            </Card>
          </div>
        </div>

        {(listError || workspaceError) && (
          <div className="mb-4 flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-amber-900">
            <AlertCircle className="mt-0.5 h-5 w-5 shrink-0" />
            <div className="text-sm">
              {listError && <p>{listError}</p>}
              {workspaceError && <p>{workspaceError}</p>}
            </div>
          </div>
        )}

        <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
          <aside className="space-y-4">
            <Card className="border-slate-200/80 bg-white/90 shadow-[0_12px_40px_rgba(15,23,42,0.08)]">
              <CardHeader>
                <CardTitle className="text-lg">Result queue</CardTitle>
                <CardDescription>Only completed or evaluation-ready candidates appear here.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {listLoading ? (
                  <div className="space-y-3">
                    {Array.from({ length: 4 }).map((_, index) => (
                      <div key={index} className="h-24 animate-pulse rounded-2xl bg-slate-100" />
                    ))}
                  </div>
                ) : items.length === 0 ? (
                  <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
                    No completed interviews are ready yet. Once Pontis marks a candidate as evaluation-ready, they appear here.
                  </div>
                ) : (
                  items.map((item) => {
                    const active = item.workflowToken === selectedWorkflowToken;
                    return (
                      <button
                        key={item.workflowToken}
                        type="button"
                        onClick={() => {
                          setWorkspace(emptyWorkspace());
                          setSelectedWorkflowToken(item.workflowToken);
                          setActiveTab("video");
                        }}
                        className={[
                          "w-full rounded-2xl border p-4 text-left transition-all",
                          active
                            ? "border-sky-300 bg-sky-50 shadow-[0_12px_24px_rgba(14,165,233,0.12)]"
                            : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50",
                        ].join(" ")}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate font-semibold text-slate-950">{item.name}</p>
                            <p className="truncate text-xs text-slate-500">{item.candidateId}</p>
                          </div>
                          <Badge variant={scoreTone(item.score)}>{Math.round(item.score)}</Badge>
                        </div>
                        <div className="mt-3 grid gap-2 text-xs text-slate-600">
                          <div className="flex items-center justify-between gap-2">
                            <span>Status</span>
                            <span className="font-medium text-slate-900">{formatStatus(item.completionState || item.status)}</span>
                          </div>
                          <div className="flex items-center justify-between gap-2">
                            <span>Recommendation</span>
                            <span className="font-medium text-slate-900">{truncate(item.recommendation, 24)}</span>
                          </div>
                          <div className="flex items-center justify-between gap-2">
                            <span>Video</span>
                            <span className="font-medium text-slate-900">{item.videoAvailable ? "Available" : "Pending"}</span>
                          </div>
                        </div>
                      </button>
                    );
                  })
                )}
              </CardContent>
            </Card>

            <Card className="border-slate-200/80 bg-white/90 shadow-[0_12px_40px_rgba(15,23,42,0.08)]">
              <CardHeader>
                <CardTitle className="text-lg">Pipeline state</CardTitle>
                <CardDescription>Recruiter workflow and result availability state.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm text-slate-600">
                <div className="flex items-center justify-between rounded-2xl bg-slate-50 px-3 py-2">
                  <span className="inline-flex items-center gap-2"><Clock3 className="h-4 w-4 text-slate-400" /> Current status</span>
                  <span className="font-medium text-slate-900">{formatStatus(workspace.status)}</span>
                </div>
                <div className="flex items-center justify-between rounded-2xl bg-slate-50 px-3 py-2">
                  <span className="inline-flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-slate-400" /> Source of truth</span>
                  <span className="font-medium text-slate-900">Pontis</span>
                </div>
                <div className="flex items-center justify-between rounded-2xl bg-slate-50 px-3 py-2">
                  <span className="inline-flex items-center gap-2"><BarChart3 className="h-4 w-4 text-slate-400" /> Evaluation</span>
                  <span className="font-medium text-slate-900">{workspace.recording.videoAvailable ? "Ready" : "Processing"}</span>
                </div>
                <Button
                  className="w-full justify-center"
                  variant="outline"
                  onClick={() => void loadResults()}
                  disabled={listLoading || workspaceLoading}
                >
                  Refresh results
                </Button>
              </CardContent>
            </Card>

            <Card className="border-slate-200/80 bg-white/90 shadow-[0_12px_40px_rgba(15,23,42,0.08)]">
              <CardHeader>
                <CardTitle className="text-lg">Recruiter actions</CardTitle>
                <CardDescription>Quick decisions and the next-step scheduling handoff.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <Button className="w-full justify-center" variant="outline" onClick={() => void handleDecision("pass")} disabled={Boolean(actionLoading)}>
                  Pass
                </Button>
                <Button className="w-full justify-center" variant="default" onClick={() => setAdvanceModalOpen(true)} disabled={Boolean(actionLoading)}>
                  Advance
                </Button>
                <Button className="w-full justify-center" variant="outline" onClick={() => void handleDecision("hold")} disabled={Boolean(actionLoading)}>
                  Hold
                </Button>
                <Button className="w-full justify-center" variant="outline" onClick={() => void handleDecision("reject")} disabled={Boolean(actionLoading)}>
                  Reject
                </Button>
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
                  <p className="font-medium text-slate-900">Workflow token</p>
                  <p className="mt-1 break-all">{selectedWorkflowToken || "Select a candidate"}</p>
                  <p className="mt-3 text-xs uppercase tracking-[0.16em] text-slate-500">Owner</p>
                  <p className="mt-1">{recruiterEmail || "Recruiter not loaded"}</p>
                </div>
              </CardContent>
            </Card>
          </aside>

          <section className="space-y-4">
            <Card className="overflow-hidden border-slate-200/80 bg-white/90 shadow-[0_20px_60px_rgba(15,23,42,0.10)]">
              <CardHeader className="border-b border-slate-200/80 bg-white/70">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div>
                    <CardTitle className="text-2xl">{activeCandidate?.name || workspace.candidate.name || "Select a candidate"}</CardTitle>
                    <CardDescription className="mt-1 max-w-2xl">
                      {truncate(workspace.summary || workspace.candidate.summary || "Interview results and recruiter intelligence will appear here once the result loads.", 220)}
                    </CardDescription>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={scoreTone(workspace.scores.overall)}>{Math.round(workspace.scores.overall)} overall</Badge>
                    <Badge variant="neutral">{formatStatus(workspace.status || activeCandidate?.completionState || "")}</Badge>
                    {workspace.recording.videoAvailable && <Badge variant="high"><PlayCircle className="mr-1 h-3.5 w-3.5" /> Video ready</Badge>}
                    {decisionState && <Badge variant="info">{decisionState.replace(/_/g, " ")}</Badge>}
                  </div>
                </div>

                <div className="rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-slate-700">
                  <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                    <div>
                      <p className="font-medium text-slate-900">{followUpPrompt || "Recruiter decision pending"}</p>
                      {actionMessage && <p className="mt-1 text-slate-600">{actionMessage}</p>}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button variant="outline" size="sm" onClick={() => void handleDecision("pass")} disabled={Boolean(actionLoading)}>
                        Pass
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => setAdvanceModalOpen(true)} disabled={Boolean(actionLoading)}>
                        Advance
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => void handleDecision("hold")} disabled={Boolean(actionLoading)}>
                        Hold
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => void handleDecision("reject")} disabled={Boolean(actionLoading)}>
                        Reject
                      </Button>
                    </div>
                  </div>
                </div>

                <div className="mt-4 flex flex-wrap gap-2">
                  {TABS.map((tab) => {
                    const Icon = tab.icon;
                    const active = tab.key === activeTab;
                    return (
                      <button
                        key={tab.key}
                        type="button"
                        onClick={() => setActiveTab(tab.key)}
                        className={[
                          "inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition-all",
                          active
                            ? "bg-slate-950 text-white shadow-[0_10px_24px_rgba(15,23,42,0.18)]"
                            : "bg-slate-100 text-slate-600 hover:bg-slate-200 hover:text-slate-900",
                        ].join(" ")}
                      >
                        <Icon className="h-4 w-4" />
                        {tab.label}
                      </button>
                    );
                  })}
                </div>
              </CardHeader>

              <CardContent className="space-y-6 p-6">
                {workspaceLoading && (
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                    Loading candidate intelligence from Pontis through Adam...
                  </div>
                )}

                {activeTab === "video" && (
                  <div className="space-y-4">
                    <InterviewRecordingPlayer
                      workflowToken={selectedWorkflowToken}
                      available={Boolean(workspace.recording.videoAvailable)}
                    />
                    <div className="grid gap-4 md:grid-cols-3">
                      <Card className="border-slate-200 bg-slate-50">
                        <CardContent className="p-4">
                          <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Session token</p>
                          <p className="mt-2 break-all text-sm font-medium text-slate-900">{workspace.recording.sessionToken || "Pending"}</p>
                        </CardContent>
                      </Card>
                      <Card className="border-slate-200 bg-slate-50">
                        <CardContent className="p-4">
                          <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Decision</p>
                          <p className="mt-2 text-sm font-medium text-slate-900">{workspace.decision || "Pending"}</p>
                        </CardContent>
                      </Card>
                      <Card className="border-slate-200 bg-slate-50">
                        <CardContent className="p-4">
                          <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Recording path</p>
                          <p className="mt-2 break-all text-sm font-medium text-slate-900">{workspace.recording.recordingPath || "Hidden by proxy"}</p>
                        </CardContent>
                      </Card>
                    </div>
                  </div>
                )}

                {activeTab === "analysis" && (
                  <div className="grid gap-4 lg:grid-cols-[1.15fr_0.85fr]">
                    <div className="space-y-4">
                      <Card className="border-slate-200 bg-gradient-to-br from-slate-50 to-white">
                        <CardContent className="space-y-4 p-5">
                          <div className="flex items-center justify-between">
                            <div>
                              <p className="text-xs uppercase tracking-[0.18em] text-slate-500">AI summary</p>
                              <p className="mt-2 text-base leading-7 text-slate-800">
                                {workspace.summary || "Pontis has not returned a summary yet."}
                              </p>
                            </div>
                            <Badge variant={scoreTone(workspace.scores.overall)}>{Math.round(workspace.scores.overall)} score</Badge>
                          </div>
                          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                            {[
                              { label: "Overall", value: workspace.scores.overall },
                              { label: "Technical", value: workspace.scores.technical },
                              { label: "Communication", value: workspace.scores.communication },
                              { label: "Culture fit", value: workspace.scores.cultureFit },
                            ].map((item) => (
                              <div key={item.label} className="rounded-2xl border border-slate-200 bg-white p-4">
                                <p className="text-xs uppercase tracking-[0.16em] text-slate-500">{item.label}</p>
                                <p className="mt-2 text-2xl font-semibold text-slate-950">{Math.round(item.value)}</p>
                              </div>
                            ))}
                          </div>
                        </CardContent>
                      </Card>

                      <div className="grid gap-4 md:grid-cols-2">
                        <Card className="border-emerald-200 bg-emerald-50/60">
                          <CardHeader className="pb-3">
                            <CardTitle className="text-base">Strengths</CardTitle>
                          </CardHeader>
                          <CardContent className="space-y-2">
                            {strengths.length > 0 ? strengths.map((item) => <p key={item} className="rounded-xl bg-white px-3 py-2 text-sm text-slate-800">{item}</p>) : <p className="text-sm text-slate-600">No strengths returned yet.</p>}
                          </CardContent>
                        </Card>
                        <Card className="border-rose-200 bg-rose-50/60">
                          <CardHeader className="pb-3">
                            <CardTitle className="text-base">Weaknesses</CardTitle>
                          </CardHeader>
                          <CardContent className="space-y-2">
                            {weaknesses.length > 0 ? weaknesses.map((item) => <p key={item} className="rounded-xl bg-white px-3 py-2 text-sm text-slate-800">{item}</p>) : <p className="text-sm text-slate-600">No weaknesses returned yet.</p>}
                          </CardContent>
                        </Card>
                      </div>
                    </div>

                    <div className="space-y-4">
                      <Card className="border-slate-200 bg-slate-50">
                        <CardHeader className="pb-3">
                          <CardTitle className="text-base">Hiring recommendation</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3">
                          <div className="rounded-2xl bg-white p-4">
                            <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Recommendation</p>
                            <p className="mt-2 text-sm text-slate-800">{workspace.recommendation || workspace.decision || "Pending"}</p>
                          </div>
                          <div className="rounded-2xl bg-white p-4">
                            <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Risk areas</p>
                            <div className="mt-3 space-y-2">
                              {riskAreas.length > 0 ? riskAreas.map((item) => <p key={item} className="rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-800">{item}</p>) : <p className="text-sm text-slate-600">No risk areas returned yet.</p>}
                            </div>
                          </div>
                        </CardContent>
                      </Card>

                      <Card className="border-slate-200 bg-slate-50">
                        <CardHeader className="pb-3">
                          <CardTitle className="text-base">Focus areas</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3 text-sm text-slate-700">
                          <div className="rounded-2xl bg-white p-4">
                            <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Communication</p>
                            <p className="mt-2 leading-6">{workspace.analysis.communication || "Awaiting analysis."}</p>
                          </div>
                          <div className="rounded-2xl bg-white p-4">
                            <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Technical depth</p>
                            <p className="mt-2 leading-6">{workspace.analysis.technicalDepth || "Awaiting analysis."}</p>
                          </div>
                        </CardContent>
                      </Card>
                    </div>
                  </div>
                )}

                {activeTab === "transcript" && (
                  <div className="space-y-4">
                    <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                      {transcriptSegments.length} transcript segment{transcriptSegments.length === 1 ? "" : "s"} parsed from Pontis plain text.
                    </div>
                    <div className="max-h-[680px] space-y-3 overflow-y-auto pr-1">
                      {transcriptSegments.length > 0 ? (
                        transcriptSegments.map((segment, index) => (
                          <div key={`${segment.timestamp || "t"}-${index}`} className={`rounded-2xl border p-4 ${segmentSpeakerTone(segment)}`}>
                            <div className="flex items-center justify-between gap-3">
                              <p className="text-sm font-semibold">{segment.speaker}</p>
                              {segment.timestamp && <p className="text-xs uppercase tracking-[0.16em] text-slate-500">{segment.timestamp}</p>}
                            </div>
                            <p className="mt-2 text-sm leading-6">{segment.text}</p>
                          </div>
                        ))
                      ) : (
                        <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-6 text-sm text-slate-600">
                          Transcript not available yet.
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {activeTab === "resume" && (
                  <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
                    <Card className="border-slate-200 bg-slate-50">
                      <CardHeader className="pb-3">
                        <CardTitle className="text-base">Candidate snapshot</CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-3 text-sm text-slate-700">
                        <div className="rounded-2xl bg-white p-4">
                          <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Name</p>
                          <p className="mt-2 font-medium text-slate-950">{workspace.candidate.name || "Unknown"}</p>
                        </div>
                        <div className="rounded-2xl bg-white p-4">
                          <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Role</p>
                          <p className="mt-2 font-medium text-slate-950">{workspace.candidate.role || "Not available"}</p>
                        </div>
                        <div className="rounded-2xl bg-white p-4">
                          <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Current company</p>
                          <p className="mt-2 font-medium text-slate-950">{workspace.candidate.company || "Not available"}</p>
                        </div>
                        <div className="rounded-2xl bg-white p-4">
                          <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Contact</p>
                          <p className="mt-2 font-medium text-slate-950">{workspace.candidate.email || "Hidden"}</p>
                        </div>
                      </CardContent>
                    </Card>

                    <div className="space-y-4">
                      <Card className="border-slate-200 bg-white">
                        <CardHeader className="pb-3">
                          <CardTitle className="text-base">Summary</CardTitle>
                        </CardHeader>
                        <CardContent>
                          <p className="whitespace-pre-wrap text-sm leading-7 text-slate-700">
                            {workspace.candidate.summary || "Resume summary not available."}
                          </p>
                        </CardContent>
                      </Card>
                      <Card className="border-slate-200 bg-white">
                        <CardHeader className="pb-3">
                          <CardTitle className="text-base">Skills</CardTitle>
                        </CardHeader>
                        <CardContent className="flex flex-wrap gap-2">
                          {workspace.candidate.skills.length > 0 ? (
                            workspace.candidate.skills.map((skill) => (
                              <span key={skill} className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm text-slate-700">
                                {skill}
                              </span>
                            ))
                          ) : (
                            <p className="text-sm text-slate-600">No skills returned yet.</p>
                          )}
                        </CardContent>
                      </Card>
                    </div>
                  </div>
                )}

                {activeTab === "timeline" && (
                  <div className="space-y-3">
                    {timelineRows.length > 0 ? (
                      timelineRows.map((row, index) => {
                        const label = String(row.type || row.eventType || row.status || "event");
                        const createdAt = String(row.createdAt || row.timestamp || row.updatedAt || "");
                        return (
                          <div key={`${label}-${index}`} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                              <p className="font-medium text-slate-950">{label.replace(/_/g, " ")}</p>
                              <p className="text-xs uppercase tracking-[0.16em] text-slate-500">{createdAt ? new Date(createdAt).toLocaleString() : "Timeline event"}</p>
                            </div>
                            <pre className="mt-3 overflow-x-auto rounded-2xl bg-white p-3 text-xs leading-6 text-slate-700">
                              {JSON.stringify(row, null, 2)}
                            </pre>
                          </div>
                        );
                      })
                    ) : (
                      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-6 text-sm text-slate-600">
                        No timeline events returned yet.
                      </div>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          </section>
        </div>

        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-slate-200/80 bg-white/90 px-4 py-3 shadow-[0_-10px_30px_rgba(15,23,42,0.08)] backdrop-blur">
          <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-3 lg:flex-row lg:items-center lg:justify-between lg:px-4">
            <div className="flex items-center gap-3 text-sm text-slate-700">
              <RectangleEllipsis className="h-4 w-4 text-slate-500" />
              <div>
                <p className="font-medium text-slate-900">Recruiter decision workspace</p>
                <p>{actionMessage || followUpPrompt || "Choose a decision, then schedule the next round if needed."}</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={() => void handleDecision("pass")} disabled={Boolean(actionLoading)}>
                Pass
              </Button>
              <Button variant="default" size="sm" onClick={() => setAdvanceModalOpen(true)} disabled={Boolean(actionLoading)}>
                Advance
              </Button>
              <Button variant="outline" size="sm" onClick={() => void handleDecision("hold")} disabled={Boolean(actionLoading)}>
                Hold
              </Button>
              <Button variant="outline" size="sm" onClick={() => void handleDecision("reject")} disabled={Boolean(actionLoading)}>
                Reject
              </Button>
            </div>
          </div>
        </div>
      </main>

      <SecondRoundSchedulingModal
        open={advanceModalOpen}
        onOpenChange={setAdvanceModalOpen}
        candidateName={activeCandidate?.name || workspace.candidate.name || "Candidate"}
        role={workspace.candidate.role || workspace.candidate.headline || "Candidate"}
        company={workspace.candidate.company || "Company"}
        defaultRecruiterEmail={recruiterEmail}
        submitting={actionLoading === "advance"}
        onSubmit={(values) => void handleAdvanceSubmit(values)}
      />
    </div>
  );
}

export default function ResultsPage() {
  return (
    <Suspense fallback={<div className="mx-auto w-full max-w-7xl px-4 py-6 text-sm text-gray-600">Loading results page...</div>}>
      <ResultsPageContent />
    </Suspense>
  );
}
