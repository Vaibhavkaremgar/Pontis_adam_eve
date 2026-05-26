"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Brain, FileText, Video } from "lucide-react";

import { AppShell } from "@/components/layout/app-shell";
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

type TabKey = "analysis" | "video" | "transcript";

// ── helpers ──────────────────────────────────────────────────────────────────

function fmt(dt: string | null | undefined) {
  if (!dt) return "";
  try {
    return new Date(dt).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
  });
  } catch {
    return dt;
  }
}

function queueBadgeVariant(state: string): "high" | "low" | "medium" | "neutral" | "info" {
  if (state === "results_ready" || state === "interview_completed") return "high";
  if (state === "rejected") return "low";
  if (state === "advanced" || state === "second_round_scheduled") return "medium";
  return "neutral";
}

function queueBadgeLabel(state: string) {
  const map: Record<string, string> = {
    interview_completed: "Completed",
    results_ready: "Completed",
    advanced: "Selected",
    second_round_requested: "2nd Round",
    second_round_scheduled: "2nd Round",
    rejected: "Rejected",
    offer_sent: "Offer Sent",
    placed: "Placed",
  };
  return map[state] ?? state.replace(/_/g, " ");
}

function scoreColor(score: number) {
  if (score >= 8) return "text-green-600";
  if (score >= 6) return "text-amber-500";
  return "text-red-500";
}

function ScoreBar({ label, score, max = 10 }: { label: string; score: number; max?: number }) {
  const pct = Math.min(100, Math.round((score / max) * 100));
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-slate-700">{label}</span>
        <span className={`text-lg font-bold ${scoreColor(score)}`}>{score.toFixed(1)}/{max}</span>
      </div>
      <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full bg-blue-600 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
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

// ── main component ────────────────────────────────────────────────────────────

function ResultsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, isSessionReady, jobId, setJobId } = useAppContext();

  const queryJobId = String(searchParams.get("jobId") || "").trim();
  const effectiveJobId = jobId || queryJobId;

  const [items, setItems] = useState<ResultListItem[]>([]);
  const [selectedToken, setSelectedToken] = useState("");
  const [workspace, setWorkspace] = useState<ResultWorkspaceResponse>(emptyWorkspace());
  const [activeTab, setActiveTab] = useState<TabKey>("analysis");
  const [listLoading, setListLoading] = useState(true);
  const [wsLoading, setWsLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState("");
  const [actionMsg, setActionMsg] = useState("");

  const selectedItem = useMemo(
    () => items.find((i) => i.workflowToken === selectedToken) ?? items[0] ?? null,
    [items, selectedToken],
  );

  // load list
  const loadList = async () => {
    if (!effectiveJobId || !user) return;
    setListLoading(true);
    const res = await getResultsList(effectiveJobId);
    if (res.success && res.data) {
      setItems(res.data.candidates);
      const first = res.data.candidates[0]?.workflowToken ?? "";
      setSelectedToken((cur) => cur || first);
    }
    setListLoading(false);
  };

  // load workspace
  const loadWorkspace = async (token: string) => {
    if (!token) return;
    setWsLoading(true);
    const res = await getResultWorkspace(token);
    if (res.success && res.data) setWorkspace(res.data);
    else setWorkspace(emptyWorkspace());
    setWsLoading(false);
  };

  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) { router.replace("/login"); return; }
    if (!effectiveJobId) { router.replace("/job"); return; }
    void loadList();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveJobId, isSessionReady]);

  useEffect(() => {
    if (jobId || !queryJobId) return;
    setJobId(queryJobId);
  }, [jobId, queryJobId, setJobId]);

  useEffect(() => {
    if (!selectedToken) return;
    setActionMsg("");
    void loadWorkspace(selectedToken);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedToken]);

  const handleDecision = async (decision: "pass" | "hold" | "reject") => {
    if (!selectedToken) return;
    setActionLoading(decision);
    setActionMsg("");
    const res = await submitResultDecision(selectedToken, { decision });
    setActionMsg(res.success ? `Decision recorded: ${decision}` : res.error ?? "Failed");
    if (res.success) { await loadList(); await loadWorkspace(selectedToken); }
    setActionLoading("");
  };

  const handleApprove = async () => {
    if (!selectedToken) return;
    setActionLoading("advance");
    setActionMsg("");
    const res = await advanceResultWorkflow(selectedToken, {
      roundType: "second_round",
      mode: "video",
      meetUrl: "",
      officeAddress: "",
      interviewer: { name: "", email: "" },
      recruiterEmail: user?.email ?? "",
      slots: [],
      notes: "",
    });
    setActionMsg(res.success ? "Candidate approved and advanced." : res.error ?? "Failed");
    if (res.success) { await loadList(); await loadWorkspace(selectedToken); }
    setActionLoading("");
  };

  // video src — proxied through Adam backend using workflow token
  const videoSrc = workspace.recording.videoAvailable && selectedToken
    ? `/api/backend/results/video/${encodeURIComponent(selectedToken)}`
    : null;

  const scores = workspace.scores;
  const scoreRows = [
    { label: "Overall", value: scores.overall },
    { label: "Technical", value: scores.technical },
    { label: "Communication", value: scores.communication },
    { label: "Culture Fit", value: scores.cultureFit },
  ];

  return (
    <AppShell activeStep={5}>
      <div className="flex h-[calc(100vh-120px)] gap-0 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">

        {/* ── LEFT: Interview Queue ─────────────────────────────────────── */}
        <aside className="flex w-[300px] shrink-0 flex-col border-r border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-5 py-4">
            <p className="text-base font-bold text-slate-900">Interview Queue</p>
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {listLoading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-24 animate-pulse rounded-xl bg-slate-100" />
              ))
            ) : items.length === 0 ? (
              <p className="p-4 text-sm text-slate-500">No completed interviews yet.</p>
            ) : (
              items.map((item) => {
                const active = item.workflowToken === selectedToken;
                return (
                  <button
                    key={item.workflowToken}
                    type="button"
                    onClick={() => { setWorkspace(emptyWorkspace()); setSelectedToken(item.workflowToken); setActiveTab("analysis"); }}
                    className={[
                      "w-full rounded-xl border p-4 text-left transition-all",
                      active ? "border-blue-400 bg-blue-50 shadow-sm" : "border-slate-200 bg-white hover:bg-slate-50",
                    ].join(" ")}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="font-semibold text-slate-900 leading-tight">{item.name}</p>
                      <Badge variant={queueBadgeVariant(item.completionState || item.status)}>
                        {queueBadgeLabel(item.completionState || item.status)}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-slate-500 truncate">{item.recommendation ? item.recommendation.slice(0, 40) : ""}</p>
                    <div className="mt-2 flex items-center justify-between text-xs text-slate-500">
                      <span>Status</span>
                      <span className="font-medium text-slate-700">{queueBadgeLabel(item.completionState || item.status)}</span>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </aside>

        {/* ── RIGHT: Workspace ─────────────────────────────────────────── */}
        <main className="flex flex-1 flex-col overflow-hidden">

          {/* header */}
          <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
            <div>
              <h2 className="text-xl font-bold text-slate-900">
                {selectedItem?.name || workspace.candidate.name || "Select a candidate"}
              </h2>
              <p className="mt-0.5 text-sm text-slate-500">
                {workspace.candidate.role || "General Interview"}
              </p>
            </div>
            <div className="flex items-center gap-3">
              <Button
                className="h-10 rounded-full bg-green-600 px-5 text-sm font-semibold text-white hover:bg-green-700"
                disabled={Boolean(actionLoading)}
                onClick={() => void handleApprove()}
              >
                {actionLoading === "advance" ? "Saving..." : "Approve"}
              </Button>
              <Button
                className="h-10 rounded-full bg-red-600 px-5 text-sm font-semibold text-white hover:bg-red-700"
                disabled={Boolean(actionLoading)}
                onClick={() => void handleDecision("reject")}
              >
                {actionLoading === "reject" ? "Saving..." : "Reject"}
              </Button>
            </div>
          </div>

          {actionMsg && (
            <div className="mx-6 mt-3 rounded-xl border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-800">
              {actionMsg}
            </div>
          )}

          {/* tabs */}
          <div className="flex gap-1 border-b border-slate-200 px-6 pt-3">
            {([ 
              { key: "analysis" as TabKey, label: "AI Analysis", icon: Brain },
              { key: "video" as TabKey, label: "Video", icon: Video },
              { key: "transcript" as TabKey, label: "Transcript", icon: FileText },
            ] as const).map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                type="button"
                onClick={() => setActiveTab(key)}
                className={[
                  "inline-flex items-center gap-2 rounded-t-lg border-b-2 px-5 py-2.5 text-sm font-medium transition-colors",
                  activeTab === key
                    ? "border-slate-900 bg-white text-slate-900"
                    : "border-transparent text-slate-500 hover:text-slate-700",
                ].join(" ")}
              >
                <Icon className="h-4 w-4" />
                {label}
              </button>
            ))}
          </div>

          {/* tab content */}
          <div className="flex-1 overflow-y-auto p-6">
            {wsLoading && (
              <div className="space-y-4">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="h-20 animate-pulse rounded-xl bg-slate-100" />
                ))}
              </div>
            )}

            {!wsLoading && activeTab === "analysis" && (
              <div className="space-y-5">
                {/* AI Summary */}
                <div className="rounded-xl border border-slate-200 bg-white p-5">
                  <div className="flex items-center gap-2 mb-3">
                    <Brain className="h-5 w-5 text-slate-700" />
                    <h3 className="font-semibold text-slate-900">AI Summary</h3>
                  </div>
                  <p className="text-sm leading-7 text-slate-700">
                    {workspace.summary || "AI summary will appear here once the interview evaluation is complete."}
                  </p>
                </div>

                {/* Score grid */}
                <div className="grid grid-cols-2 gap-4">
                  {scoreRows.map((row) => (
                    <ScoreBar key={row.label} label={row.label} score={row.value} />
                  ))}
                </div>

                {/* Strengths / Weaknesses */}
                {((workspace.analysis.strengths ?? []).length > 0 || (workspace.analysis.weaknesses ?? []).length > 0) && (
                  <div className="grid grid-cols-2 gap-4">
                    {(workspace.analysis.strengths ?? []).length > 0 && (
                      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                        <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-emerald-700">Strengths</p>
                        <ul className="space-y-1">
                          {(workspace.analysis.strengths ?? []).map((s) => (
                            <li key={s} className="text-sm text-slate-800">• {s}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {(workspace.analysis.weaknesses ?? []).length > 0 && (
                      <div className="rounded-xl border border-rose-200 bg-rose-50 p-4">
                        <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-rose-700">Weaknesses</p>
                        <ul className="space-y-1">
                          {(workspace.analysis.weaknesses ?? []).map((w) => (
                            <li key={w} className="text-sm text-slate-800">• {w}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

                {/* Recommendation */}
                {workspace.recommendation && (
                  <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                    <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-1">Recommendation</p>
                    <p className="text-sm text-slate-800">{workspace.recommendation}</p>
                  </div>
                )}
              </div>
            )}

            {!wsLoading && activeTab === "video" && (
              <div className="space-y-4">
                {videoSrc ? (
                  <div className="overflow-hidden rounded-xl border border-slate-200 bg-black">
                    {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
                    <video
                      key={videoSrc}
                      controls
                      className="w-full max-h-[520px]"
                      src={videoSrc}
                    />
                  </div>
                ) : (
                  <div className="flex h-64 items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50">
                    <div className="text-center">
                      <Video className="mx-auto h-10 w-10 text-slate-300" />
                      <p className="mt-3 text-sm text-slate-500">
                        {workspace.recording.recordingPath
                          ? "Video is processing — check back shortly."
                          : "No video recording available for this interview."}
                      </p>
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                    <p className="text-xs uppercase tracking-widest text-slate-500 mb-1">Session token</p>
                    <p className="font-medium text-slate-900 break-all">{workspace.recording.sessionToken || "—"}</p>
                  </div>
                  <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                    <p className="text-xs uppercase tracking-widest text-slate-500 mb-1">Recording status</p>
                    <p className="font-medium text-slate-900">
                      {workspace.recording.videoAvailable ? "Available" : "Pending"}
                    </p>
                  </div>
                </div>
              </div>
            )}

            {!wsLoading && activeTab === "transcript" && (
              <div className="space-y-3">
                {workspace.transcript ? (
                  workspace.transcript.split("\n").filter(Boolean).map((line, i) => {
                    const isCandidate = /^(candidate|interviewee):/i.test(line);
                    const isInterviewer = /^(interviewer|adam|recruiter):/i.test(line);
                    return (
                      <div
                        key={i}
                        className={[
                          "rounded-xl border p-4 text-sm leading-6",
                          isCandidate ? "border-sky-200 bg-sky-50 text-slate-900" :
                          isInterviewer ? "border-emerald-200 bg-emerald-50 text-slate-900" :
                          "border-slate-200 bg-slate-50 text-slate-700",
                        ].join(" ")}
                      >
                        {line}
                      </div>
                    );
                  })
                ) : (
                  <div className="flex h-48 items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50">
                    <div className="text-center">
                      <FileText className="mx-auto h-10 w-10 text-slate-300" />
                      <p className="mt-3 text-sm text-slate-500">Transcript not available yet.</p>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </main>
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
