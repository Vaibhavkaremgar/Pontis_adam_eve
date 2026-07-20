"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Briefcase, MapPin, Star, VideoOff } from "lucide-react";

import { AppShell } from "@/components/layout/app-shell";
import { InterviewRecordingPlayer } from "@/components/results/interview-recording-player";
import { SecondRoundSchedulingModal, type SecondRoundInviteValues } from "@/components/results/second-round-scheduling-modal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAppContext } from "@/context/AppContext";
import {
  advanceResultWorkflow,
  getJobsForResults,
  getResultWorkspace,
  getResultsList,
  submitResultDecision,
  type JobSummary,
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

function ScoreRow({ label, score, description }: { label: string; score: number; description: string }) {
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

// ─── Level 1: Job roles list ──────────────────────────────────────────────────
function JobsList({ onSelect }: { onSelect: (job: JobSummary) => void }) {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getJobsForResults().then((res) => {
      if (res.success && res.data) setJobs(res.data.jobs);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-20 animate-pulse rounded-2xl bg-slate-100" />
        ))}
      </div>
    );
  }

  if (!jobs.length) {
    return (
      <div className="flex h-48 items-center justify-center rounded-2xl border border-dashed border-slate-200 text-sm text-slate-500">
        No hiring roles found. Start a new hire to see results here.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {jobs.map((job) => (
        <button
          key={job.jobId}
          onClick={() => onSelect(job)}
          className="group flex w-full items-center justify-between rounded-2xl border border-[rgba(120,100,80,0.1)] bg-white p-5 text-left shadow-sm transition hover:shadow-md"
        >
          <div>
            <p className="font-semibold text-slate-900">{job.title}</p>
            <p className="mt-1 text-sm text-slate-500">{job.location || "Location not set"}</p>
          </div>
          <ArrowLeft className="h-4 w-4 rotate-180 text-slate-400 transition group-hover:translate-x-1" />
        </button>
      ))}
    </div>
  );
}

// ─── Level 2: Candidates list for a job ──────────────────────────────────────
function CandidatesList({ job, onSelect, onBack }: { job: JobSummary; onSelect: (item: ResultListItem) => void; onBack: () => void }) {
  const [items, setItems] = useState<ResultListItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getResultsList(job.jobId).then((res) => {
      if (res.success && res.data) setItems(res.data.candidates || []);
      setLoading(false);
    });
  }, [job.jobId]);

  return (
    <div className="space-y-4">
      <button onClick={onBack} className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-slate-800">
        <ArrowLeft className="h-4 w-4" /> Back to roles
      </button>
      <div>
        <h2 className="text-xl font-semibold text-slate-900">{job.title}</h2>
        <p className="mt-1 text-sm text-slate-500">{job.location || "Location not set"}</p>
      </div>

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-20 animate-pulse rounded-2xl bg-slate-100" />
          ))}
        </div>
      ) : !items.length ? (
        <div className="flex h-48 items-center justify-center rounded-2xl border border-dashed border-slate-200 text-sm text-slate-500">
          No completed interviews for this role yet.
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <button
              key={item.workflowToken}
              onClick={() => onSelect(item)}
              className="group flex w-full items-center justify-between rounded-2xl border border-[rgba(120,100,80,0.1)] bg-white p-5 text-left shadow-sm transition hover:shadow-md"
            >
              <div className="space-y-1">
                <p className="font-semibold text-slate-900">{item.name}</p>
                <div className="flex flex-wrap items-center gap-2 text-sm text-slate-500">
                  <Badge variant={item.completionState === "results_ready" ? "high" : "neutral"}>
                    {item.completionState || item.status}
                  </Badge>
                  {item.score > 0 && (
                    <span className={`font-semibold ${scoreTone(item.score)}`}>
                      {formatScore(item.score)} / 10
                    </span>
                  )}
                  {item.recommendation && <span>{item.recommendation}</span>}
                </div>
              </div>
              <ArrowLeft className="h-4 w-4 rotate-180 text-slate-400 transition group-hover:translate-x-1" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Level 3: Candidate detail ────────────────────────────────────────────────
function CandidateDetail({
  item,
  jobTitle,
  onBack,
  user,
}: {
  item: ResultListItem;
  jobTitle: string;
  onBack: () => void;
  user: { email: string } | null;
}) {
  const [workspace, setWorkspace] = useState<ResultWorkspaceResponse>(emptyWorkspace());
  const [wsLoading, setWsLoading] = useState(true);
  const [decisionLoading, setDecisionLoading] = useState(false);
  const [selectedForSecondRound, setSelectedForSecondRound] = useState(false);
  const [inviteLoading, setInviteLoading] = useState(false);

  const resultsExpiry = useMemo(() => resolveResultsExpiry(workspace, item), [workspace, item]);
  const hasExpiredResults = Boolean(resultsExpiry && resultsExpiry.getTime() <= Date.now());
  const remainingDays = useMemo(() => {
    if (!resultsExpiry) return null;
    return Math.max(0, Math.ceil((resultsExpiry.getTime() - Date.now()) / (24 * 60 * 60 * 1000)));
  }, [resultsExpiry]);

  useEffect(() => {
    setWsLoading(true);
    getResultWorkspace(item.workflowToken).then((res) => {
      if (res.success && res.data) setWorkspace(res.data);
      else setWorkspace(emptyWorkspace());
      setWsLoading(false);
    });
  }, [item.workflowToken]);

  const handleDecision = async (decision: "pass" | "reject") => {
    if (decision === "pass") { setSelectedForSecondRound(true); return; }
    setDecisionLoading(true);
    await submitResultDecision(item.workflowToken, { decision });
    setDecisionLoading(false);
  };

  const handleSecondRoundSubmit = async (values: SecondRoundInviteValues) => {
    if (inviteLoading) return;
    setInviteLoading(true);
    await advanceResultWorkflow(item.workflowToken, {
      roundType: values.roundType,
      mode: values.mode,
      meetUrl: values.meetUrl,
      officeAddress: values.officeAddress,
      interviewer: { name: values.interviewerName, email: values.interviewerEmail },
      recruiterEmail: values.recruiterEmail,
      slots: [values.interviewDate, values.interviewTime].filter(Boolean),
      notes: values.additionalNotes,
      timezone: values.timezone,
      duration: "",
      panelInterviewers: [],
    });
    setSelectedForSecondRound(false);
    setInviteLoading(false);
  };

  const scoreLabel = labelForScore(workspace.scores.overall);
  const scoreRows = [
    { label: "Communication", score: workspace.scores.communication, description: workspace.analysis.communication || "Clear, structured, and confident responses." },
    { label: "Technical Skills", score: workspace.scores.technical, description: workspace.analysis.technicalDepth || "Shows practical problem-solving depth." },
    { label: "Culture Fit", score: workspace.scores.cultureFit, description: workspace.analysis.riskAreas?.length ? (workspace.analysis.riskAreas as string[]).join(" ") : "Aligned with team expectations." },
    { label: "Overall", score: workspace.scores.overall, description: workspace.summary || "A well-rounded interview summary appears here." },
  ];

  if (wsLoading) {
    return (
      <div className="space-y-4">
        <button onClick={onBack} className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-slate-800">
          <ArrowLeft className="h-4 w-4" /> Back to candidates
        </button>
        <div className="h-40 animate-pulse rounded-2xl bg-slate-100" />
        <div className="h-64 animate-pulse rounded-2xl bg-slate-100" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <button onClick={onBack} className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-slate-800">
        <ArrowLeft className="h-4 w-4" /> Back to candidates
      </button>

      {/* Header */}
      <section className="rounded-[28px] border border-[rgba(120,100,80,0.08)] bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
              {item.name || workspace.candidate.name || "Candidate"}
            </h1>
            <div className="flex flex-wrap items-center gap-3 text-sm text-slate-600">
              <span className="inline-flex items-center gap-2 rounded-full bg-indigo-50 px-3 py-1 font-medium text-indigo-700">
                <Briefcase className="h-4 w-4" />
                {jobTitle}
              </span>
              {workspace.candidate.location && (
                <span className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1">
                  <MapPin className="h-4 w-4" />
                  {workspace.candidate.location}
                </span>
              )}
            </div>
          </div>

          <div className="min-w-[220px] rounded-2xl border border-emerald-100 bg-emerald-50 px-5 py-4">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">
              <Star className="h-4 w-4 text-indigo-600" />
              Overall score
            </div>
            <div className="mt-2 flex items-end justify-between gap-4">
              <div className={`text-4xl font-semibold tracking-tight ${scoreTone(workspace.scores.overall)}`}>
                {formatScore(workspace.scores.overall)}
                <span className="text-lg text-slate-500">/10</span>
              </div>
              <Badge variant="high">{scoreLabel}</Badge>
            </div>
          </div>
        </div>

        <div className="mt-4 flex gap-2">
          <Button
            variant="outline"
            className="rounded-full border-emerald-200 text-emerald-700 hover:bg-emerald-50"
            onClick={() => void handleDecision("pass")}
            disabled={decisionLoading || inviteLoading}
          >
            Advance to next round
          </Button>
          <Button
            variant="outline"
            className="rounded-full border-rose-200 text-rose-700 hover:bg-rose-50"
            onClick={() => void handleDecision("reject")}
            disabled={decisionLoading || inviteLoading}
          >
            {decisionLoading ? "Saving..." : "Reject"}
          </Button>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.75fr)_minmax(300px,1fr)]">
        <div className="space-y-4">
          {hasExpiredResults ? (
            <section className="rounded-[28px] border border-amber-100 bg-amber-50 p-5">
              <div className="flex items-center gap-3">
                <div className="rounded-full bg-amber-100 p-2 text-amber-700">
                  <VideoOff className="h-5 w-5" />
                </div>
                <div>
                  <h2 className="font-semibold text-amber-950">Results expired</h2>
                  <p className="mt-1 text-sm text-amber-800">
                    Video and AI analysis are hidden after 7 days. Records remain in the database.
                  </p>
                </div>
              </div>
            </section>
          ) : (
            <>
              <InterviewRecordingPlayer
                workflowToken={item.workflowToken}
                available={Boolean(workspace.recording.videoAvailable || workspace.recording.recordingPath)}
                title="Interview recording"
                className="rounded-[28px]"
              />

              <section className="rounded-[28px] border border-[rgba(120,100,80,0.08)] bg-white p-5 shadow-sm">
                <h2 className="text-lg font-semibold text-slate-900">AI Interview Analysis</h2>
                <p className="mt-3 text-[15px] leading-7 text-slate-700">
                  {workspace.summary || "AI summary will appear here once evaluation is complete."}
                </p>
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Score justification</p>
                    <p className="mt-2 text-sm leading-6 text-slate-700">
                      {workspace.decision || "Score justification will appear here."}
                    </p>
                  </div>
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Transcript preview</p>
                    <p className="mt-2 text-sm leading-6 text-slate-700">
                      {workspace.transcript ? workspace.transcript.slice(0, 320) : "Transcript not available yet."}
                    </p>
                  </div>
                </div>
              </section>
            </>
          )}

          {remainingDays !== null && !hasExpiredResults && (
            <p className="text-xs text-slate-400">
              Results visible for {remainingDays} more day{remainingDays === 1 ? "" : "s"}.
            </p>
          )}
        </div>

        <div className="space-y-4">
          <section className="rounded-[28px] border border-[rgba(120,100,80,0.08)] bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-900">Scoring</h2>
            <div className="mt-4 space-y-5">
              {scoreRows.map((row) => <ScoreRow key={row.label} {...row} />)}
            </div>
          </section>

          <section className="rounded-[28px] border border-[rgba(120,100,80,0.08)] bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-900">Transcript</h2>
            <div className="mt-4 max-h-[420px] space-y-3 overflow-auto pr-1">
              {hasExpiredResults ? (
                <div className="flex h-40 items-center justify-center rounded-2xl border border-dashed border-amber-200 bg-amber-50 text-sm text-amber-800">
                  Transcript hidden after 7-day retention window.
                </div>
              ) : workspace.transcript ? (
                workspace.transcript.split("\n").filter(Boolean).map((line, i) => (
                  <div key={i} className="rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-700">
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

      <SecondRoundSchedulingModal
        open={selectedForSecondRound}
        onOpenChange={(open) => { if (!open) setSelectedForSecondRound(false); }}
        candidateName={item.name || ""}
        role={jobTitle}
        company={workspace.candidate.company || ""}
        defaultRecruiterEmail={user?.email || ""}
        submitting={inviteLoading}
        onSubmit={(values) => void handleSecondRoundSubmit(values)}
      />
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────
type View =
  | { level: "jobs" }
  | { level: "candidates"; job: JobSummary }
  | { level: "detail"; job: JobSummary; item: ResultListItem };

function ResultsPageContent() {
  const router = useRouter();
  const { user, isSessionReady } = useAppContext();
  const [view, setView] = useState<View>({ level: "jobs" });

  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) router.replace("/login");
  }, [isSessionReady, router, user]);

  return (
    <AppShell activeStep={5}>
      <div className="mx-auto w-full max-w-3xl px-4 py-8">
        <div className="mb-6 flex items-center gap-3">
          <button
            onClick={() => router.push("/workspace")}
            className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-slate-800"
          >
            <ArrowLeft className="h-4 w-4" /> Workspace
          </button>
          <span className="text-slate-300">/</span>
          <p className="text-sm font-semibold text-slate-700">Results</p>
          {view.level !== "jobs" && (
            <>
              <span className="text-slate-300">/</span>
              <p className="text-sm text-slate-700">{(view as { job: JobSummary }).job.title}</p>
            </>
          )}
          {view.level === "detail" && (
            <>
              <span className="text-slate-300">/</span>
              <p className="text-sm text-slate-700">{(view as { item: ResultListItem }).item.name}</p>
            </>
          )}
        </div>

        {view.level === "jobs" && (
          <>
            <h1 className="mb-4 text-2xl font-semibold text-slate-900">Hiring roles</h1>
            <JobsList onSelect={(job) => setView({ level: "candidates", job })} />
          </>
        )}

        {view.level === "candidates" && (
          <CandidatesList
            job={(view as { job: JobSummary }).job}
            onSelect={(item) => setView({ level: "detail", job: (view as { job: JobSummary }).job, item })}
            onBack={() => setView({ level: "jobs" })}
          />
        )}

        {view.level === "detail" && (
          <CandidateDetail
            item={(view as { item: ResultListItem }).item}
            jobTitle={(view as { job: JobSummary }).job.title}
            onBack={() => setView({ level: "candidates", job: (view as { job: JobSummary }).job })}
            user={user}
          />
        )}
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
