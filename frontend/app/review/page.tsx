"use client";

/**
 * What this file does:
 * Runs the 3-step candidate selection flow.
 * Shows 2 candidates at a time, records one recruiter choice per batch,
 * and then renders the preference-driven reranked result set.
 *
 * What API it connects to:
 * GET /candidates/selection/first
 * POST /candidates/selection
 * GET /candidates/selection/final
 *
 * How it fits in the pipeline:
 * Voice intake -> selection session -> recruiter preference learning -> refined shortlist -> outreach
 */
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  BriefcaseBusiness,
  Building2,
  CheckCircle2,
  ChevronDown,
  CircleUserRound,
  GraduationCap,
  MapPin,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Modal } from "@/components/ui/modal";
import { Separator } from "@/components/ui/separator";
import { useAppContext } from "@/context/AppContext";
import { getFinalSelectionResults, getFirstSelectionBatch, submitSelectionChoice } from "@/lib/api/candidates";
import type { Candidate, CandidateSelectionAnalysis, CandidateSelectionSession } from "@/types";

function statusLabel(candidate: Candidate): string {
  if (candidate.status === "shortlisted") return "Selected";
  if (candidate.status === "rejected") return "Rejected";
  return "Awaiting choice";
}

function candidateSubtitle(candidate: Candidate): string {
  return [candidate.headline || candidate.role || "", candidate.company ? candidate.company : "", candidate.location || ""]
    .filter(Boolean)
    .join(" - ");
}

function renderSignals(candidate: Candidate) {
  const explanation = candidate.explanation;
  const penalties = explanation?.penalties ?? {};
  const semantic = explanation?.semanticScore ?? explanation?.semantic ?? 0;
  const matchedSkills = explanation?.skillsMatched ?? explanation?.skills_match ?? [];
  const experienceMatch = explanation?.experienceMatch || explanation?.candidateExperience || explanation?.jobExperience || "";

  return (
    <div className="space-y-1 rounded-xl bg-white/70 p-3 text-xs text-gray-600">
      <p>
        Semantic: <span className="font-medium text-gray-800">{(semantic * 100).toFixed(0)}%</span>
      </p>
      {experienceMatch && (
        <p>
          Experience: <span className="font-medium text-gray-800">{experienceMatch}</span>
        </p>
      )}
      {matchedSkills.length > 0 && (
        <p>
          Matched skills: <span className="font-medium text-gray-800">{matchedSkills.slice(0, 4).join(", ")}</span>
        </p>
      )}
      {typeof penalties.selectionPreferenceBonus === "number" && (
        <p>
          Selection boost: <span className="font-medium text-green-700">+{penalties.selectionPreferenceBonus.toFixed(3)}</span>
        </p>
      )}
      {explanation?.aiReasoning && <p className="italic text-gray-500">{explanation.aiReasoning}</p>}
    </div>
  );
}

function analysisSummary(analysis: CandidateSelectionAnalysis | null | undefined) {
  if (!analysis) return [];
  return [
    analysis.summary,
    analysis.preferenceSignals.sharedSkills.length > 0 ? `Shared skills: ${analysis.preferenceSignals.sharedSkills.slice(0, 5).join(", ")}` : "",
    analysis.preferenceSignals.sharedRoles.length > 0 ? `Role alignment: ${analysis.preferenceSignals.sharedRoles.slice(0, 5).join(", ")}` : "",
    analysis.preferenceSignals.sharedCompanies.length > 0
      ? `Company overlap: ${analysis.preferenceSignals.sharedCompanies.slice(0, 5).join(", ")}`
      : "",
  ].filter(Boolean);
}

function formatList(values?: string[], fallback = "Not provided"): string[] {
  if (!values || values.length === 0) return [fallback];
  return values.filter(Boolean);
}

function trimText(value?: string, maxLength = 1200): string {
  if (!value) return "";
  const normalized = value.replace(/\r\n/g, "\n").trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength).trim()}...`;
}

function clampLines(lines = 2) {
  return {
    display: "-webkit-box",
    WebkitLineClamp: lines,
    WebkitBoxOrient: "vertical" as const,
    overflow: "hidden",
  };
}

function DetailRow({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-[#ECE7DE] py-2 last:border-b-0">
      <span className="text-sm text-[#6B7280]">{label}</span>
      <span className="text-sm font-semibold text-[#111827]">{value}</span>
    </div>
  );
}

function ProfileToggleButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-14 w-full items-center justify-between rounded-[16px] border border-[#ECE7DE] bg-white px-4 text-left text-[15px] font-semibold text-[#111827] transition-all duration-200 hover:bg-[#FAFAF8]"
    >
      <span className="flex items-center gap-3">
        <CircleUserRound className="h-5 w-5 text-[#0F6B3A]" />
        <span>View full profile</span>
      </span>
      <ChevronDown className="h-5 w-5 text-[#6B7280]" />
    </button>
  );
}

function CandidateDetails({ candidate }: { candidate: Candidate }) {
  return (
    <div className="space-y-5">
      <div className="rounded-[18px] border border-[#ECE7DE] bg-[#F8F7F3] p-5">
        <div className="space-y-3">
          <DetailRow label="Email" value={candidate.email || "Not provided"} />
          <DetailRow label="Role" value={candidate.headline || candidate.role || "Not provided"} />
          <DetailRow label="Location" value={candidate.location || "Not provided"} />
          <DetailRow label="Company" value={candidate.company || "Not provided"} />
          <DetailRow label="Experience" value={candidate.yearsExperience ? `${candidate.yearsExperience.toFixed(1)} years` : "Not provided"} />
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Education</p>
          <ul className="mt-3 space-y-2 text-sm text-[#4B5563]">
            {formatList(candidate.education).map((item) => (
              <li key={`education-${candidate.id}-${item}`} className="flex gap-2">
                <GraduationCap className="mt-0.5 h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Projects</p>
          <ul className="mt-3 space-y-2 text-sm text-[#4B5563]">
            {formatList(candidate.projects).map((item) => (
              <li key={`project-${candidate.id}-${item}`} className="flex gap-2">
                <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Certifications</p>
          <ul className="mt-3 space-y-2 text-sm text-[#4B5563]">
            {formatList(candidate.certifications).map((item) => (
              <li key={`cert-${candidate.id}-${item}`} className="flex gap-2">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Companies</p>
          <ul className="mt-3 space-y-2 text-sm text-[#4B5563]">
            {formatList(candidate.companiesHistory).map((item) => (
              <li key={`company-${candidate.id}-${item}`} className="flex gap-2">
                <Building2 className="mt-0.5 h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5 md:col-span-2">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Domain experience</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {formatList(candidate.domainExperience).map((item) => (
              <span key={`domain-${candidate.id}-${item}`} className="rounded-full bg-[#F5E7B8] px-3 py-1 text-xs font-semibold text-[#8A5A00]">
                {item}
              </span>
            ))}
          </div>
        </div>
      </div>

      {candidate.summary && (
        <div className="rounded-[18px] border border-[#ECE7DE] bg-[#F8F7F3] p-5">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Summary</p>
          <p className="mt-3 text-sm leading-7 text-[#4B5563]">{trimText(candidate.summary, 1200)}</p>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5 text-sm text-[#4B5563]">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Scoring</p>
          <div className="mt-3 space-y-0.5">
            <DetailRow label="Fit score" value={`${candidate.fitScore.toFixed(1)} / 5`} />
            <DetailRow label="Status" value={statusLabel(candidate)} />
            <DetailRow label="Strategy" value={candidate.strategy} />
          </div>
        </div>

        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5 text-sm text-[#4B5563]">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Signals</p>
          <div className="mt-3 space-y-0.5">
            <DetailRow label="Resume included" value={candidate.resumeText ? "Yes" : "No"} />
            <DetailRow label="Mock email" value={candidate.isMockEmail ? "Yes" : "No"} />
            <DetailRow label="Matched skills" value={candidate.skills.length ? `${candidate.skills.slice(0, 4).join(", ")}` : "Not provided"} />
          </div>
        </div>
      </div>

      {candidate.resumeText && (
        <div className="rounded-[18px] border border-[#ECE7DE] bg-[#111111] p-5 text-sm text-white">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/70">Resume text</p>
          <pre className="mt-3 whitespace-pre-wrap font-body leading-relaxed text-white/90">{candidate.resumeText}</pre>
        </div>
      )}

      {candidate.explanation?.sourceBreakdown && (
        <div className="grid gap-2 rounded-[18px] border border-[#ECE7DE] bg-white p-5 text-xs text-[#6B7280] sm:grid-cols-2">
          <span>Vector: {(candidate.explanation.sourceBreakdown.vector ?? 0).toFixed(2)}</span>
          <span>Lexical: {(candidate.explanation.sourceBreakdown.lexical ?? 0).toFixed(2)}</span>
          <span>Recruiter: {(candidate.explanation.sourceBreakdown.recruiterPreference ?? 0).toFixed(2)}</span>
          <span>Selection: {(candidate.explanation.sourceBreakdown.selectionRound ?? 0).toFixed(2)}</span>
          <span>Voice: {(candidate.explanation.sourceBreakdown.voiceInterview ?? 0).toFixed(2)}</span>
          <span>Freshness: {(candidate.explanation.sourceBreakdown.freshness ?? 0).toFixed(2)}</span>
        </div>
      )}
    </div>
  );
}

function CandidateCard({
  candidate,
  isSelected,
  isSelecting,
  selectionLocked,
  onOpenDetails,
  onSelect,
  showSelectButton,
}: {
  candidate: Candidate;
  isSelected: boolean;
  isSelecting: boolean;
  selectionLocked: boolean;
  onOpenDetails: () => void;
  onSelect?: () => void;
  showSelectButton: boolean;
}) {
  return (
    <Card
      role="button"
      tabIndex={0}
      onClick={onOpenDetails}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpenDetails();
        }
      }}
      className={`flex h-full min-h-[620px] flex-col rounded-[24px] border border-[#E7E0D4] bg-white p-8 shadow-[0_8px_24px_rgba(0,0,0,0.04)] transition-all duration-200 hover:-translate-y-1 hover:shadow-[0_14px_32px_rgba(0,0,0,0.08)] ${
        isSelected ? "ring-2 ring-[#DDF5E6]" : ""
      }`}
    >
      <CardHeader className="p-0">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 space-y-3">
            <CardTitle className="font-heading text-[32px] font-bold leading-none text-[#111827]">
              {candidate.name || candidate.id.slice(0, 8)}
            </CardTitle>
            <div className="space-y-2 text-[16px] leading-6 text-[#4B5563]">
              <p className="flex items-center gap-2">
                <BriefcaseBusiness className="h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span style={clampLines(2)}>
                  {candidate.headline || candidate.role || "Not provided"}
                </span>
              </p>
              <p className="flex items-center gap-2">
                <Building2 className="h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span>{candidate.company || "Not provided"}</span>
              </p>
              <p className="flex items-center gap-2">
                <MapPin className="h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span>{candidate.location || "Not provided"}</span>
              </p>
            </div>
          </div>
          <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-[#DDF5E6] text-center text-[16px] font-semibold leading-tight text-[#0F6B3A]">
            <span>{candidate.fitScore.toFixed(1)}</span>
            <span className="block text-[12px]">/5</span>
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex flex-1 flex-col p-0 pt-6">
        <div className="mb-4 inline-flex w-fit rounded-full bg-[#F3F4F6] px-3 py-1 text-xs font-semibold text-[#6B7280]">
          {statusLabel(candidate)}
        </div>

        <div className="rounded-[18px] border border-[#ECE7DE] bg-[#F8F7F3] p-5">
          <DetailRow label="Experience" value={candidate.yearsExperience ? `${candidate.yearsExperience.toFixed(1)} years` : "Not provided"} />
          <DetailRow label="Company" value={candidate.company || "Not provided"} />
        </div>

        <div className="mt-5">
        <ProfileToggleButton
            onClick={() => onOpenDetails()}
          />
        </div>

        <div className="mt-auto pt-6">
          {showSelectButton && onSelect && (
            <Button
              className="h-14 w-full rounded-[14px] bg-[#0F6B3A] text-[18px] font-semibold text-white shadow-[0_8px_18px_rgba(15,107,58,0.18)] transition-colors duration-200 hover:bg-[#0C5A31]"
              onClick={(event) => {
                event.stopPropagation();
                onSelect();
              }}
              disabled={selectionLocked || isSelecting}
            >
              {isSelecting ? "Saving choice..." : "Select this candidate"}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function ReviewPage() {
  const router = useRouter();
  const { user, isSessionReady, jobId, isRefined } = useAppContext();

  const [session, setSession] = useState<CandidateSelectionSession | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isAdvancing, setIsAdvancing] = useState(false);
  const [error, setError] = useState("");
  const [selectionDebug, setSelectionDebug] = useState("");
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [activeCandidate, setActiveCandidate] = useState<Candidate | null>(null);

  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    if (!jobId) {
      router.replace("/job");
    }
  }, [isSessionReady, jobId, router, user]);

  useEffect(() => {
    if (!isSessionReady || !user || !jobId) return;

    let cancelled = false;
    const load = async () => {
      setIsLoading(true);
      setError("");

      const result = await getFirstSelectionBatch(jobId);
      if (cancelled) return;

      if (!result.success || !result.data) {
        setError(result.error || "Could not load candidate selection.");
        setSelectionDebug("");
        setIsLoading(false);
        return;
      }

      setSession(result.data);
      setActiveCandidate(null);
      setIsLoading(false);
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [isSessionReady, jobId, user]);

  const currentBatch = session?.currentBatch ?? [];
  const completed = Boolean(session?.completed);
  const progress = session ? Math.min(session.currentBatchIndex + (completed ? 0 : 1), session.totalBatches) : 0;
  const finalCandidates = session?.finalCandidates ?? session?.topCandidates ?? [];
  const analysis = session?.analysis ?? null;
  const summaryLines = useMemo(() => analysisSummary(analysis), [analysis]);

  const handleSelect = async (candidateId: string) => {
    if (!jobId || !session || isAdvancing || completed) return;
    setIsAdvancing(true);
    setError("");
    setSelectedCandidateId(candidateId);

    const result = await submitSelectionChoice({ jobId, candidateId });
    if (!result.success || !result.data) {
      const debugText = [
        `jobId=${jobId}`,
        `candidateId=${candidateId}`,
        `selectedBatch=${(session?.currentBatch || []).map((item) => item.id).join(", ")}`,
        `error=${result.error || "Unknown error"}`,
      ].join("\n");
      console.error("[selection] submit failed", {
        jobId,
        candidateId,
        selectedBatch: session?.currentBatch,
        response: result,
      });
      setError(result.error || "Could not record candidate selection.");
      setSelectionDebug(debugText);
      setIsAdvancing(false);
      setSelectedCandidateId("");
      return;
    }

    setSession(result.data);
    setActiveCandidate(null);
    if (result.data.warning) {
      console.warn("[selection] submit warning", result.data.warning);
      setSelectionDebug(`warning=${result.data.warning}`);
    } else {
      setSelectionDebug("");
    }
    setIsAdvancing(false);
    setSelectedCandidateId("");
  };

  const refreshFinalResults = async () => {
    if (!jobId) return;
    setIsLoading(true);
    const result = await getFinalSelectionResults(jobId);
    if (result.success && result.data) {
      setSession(result.data);
      setActiveCandidate(null);
    } else if (result.error) {
      setError(result.error);
    }
    setIsLoading(false);
  };

  return (
    <AppShell activeStep={4}>
      <div className="mx-auto w-full max-w-[1600px] space-y-6 px-4 py-6 md:px-8 2xl:px-6">
        <div className="w-full rounded-[32px] border border-[#E7E0D4] bg-[#F8F5EE] p-5 shadow-[0_8px_24px_rgba(0,0,0,0.04)] md:p-6 lg:p-10">
          <div className="mb-6 flex items-center justify-between gap-4">
            <div className="space-y-1">
              <h1 className="font-heading text-3xl font-bold tracking-tight text-[#111827]">Review Candidates</h1>
              <p className="text-sm text-[#6B7280]">A refined shortlist review built for fast, confident selection.</p>
            </div>
          </div>

          {isRefined && (
            <div className="rounded-[20px] border border-[#DDF5E6] bg-[#F4FBF7] px-4 py-3 text-sm text-[#0F6B3A]">
              Voice intake completed. The selection flow is now running on the refined job profile.
            </div>
          )}

          <div className="grid h-[72px] grid-cols-3 overflow-hidden rounded-[20px] border border-[#E7E0D4] bg-white shadow-[0_8px_24px_rgba(0,0,0,0.04)]">
            <div className="flex items-center justify-center gap-3 border-r border-[#ECE7DE] px-4">
              <ShieldCheck className="h-5 w-5 text-[#0F6B3A]" />
              <span className="text-[18px] font-semibold text-[#111827]">
                Progress: <span className="text-[#0F6B3A]">{session ? `${progress} / ${session.totalBatches}` : "0 / 3"}</span>
              </span>
            </div>
            <div className="flex items-center justify-center gap-3 border-r border-[#ECE7DE] px-4">
              <CheckCircle2 className="h-5 w-5 text-[#0F6B3A]" />
              <span className="text-[18px] font-semibold text-[#111827]">
                Selected: <span className="text-[#0F6B3A]">{session?.selectedCandidateIds.length ?? 0}</span>
              </span>
            </div>
            <div className="flex items-center justify-center gap-3 px-4">
              <ShieldCheck className="h-5 w-5 text-[#0F6B3A]" />
              <span className="text-[18px] font-semibold text-[#111827]">
                Rejected: <span className="text-[#0F6B3A]">{session?.rejectedCandidateIds.length ?? 0}</span>
              </span>
            </div>
          </div>

          {isLoading && <p className="text-sm text-gray-500">Loading selection session...</p>}
          {error && <p className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
          {selectionDebug && (
            <details className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <summary className="cursor-pointer font-medium">Debug details</summary>
              <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs leading-relaxed text-amber-950">{selectionDebug}</pre>
            </details>
          )}

          {!isLoading && !completed && currentBatch.length > 0 && (
            <div className="space-y-8">
              <div className="flex flex-col items-start justify-between gap-4 md:flex-row md:items-center">
                <div className="space-y-2">
                  <p className="text-xs font-semibold uppercase tracking-[0.24em] text-[#0F6B3A]">
                    Batch {session?.currentBatchIndex ? session.currentBatchIndex + 1 : 1} of {session?.totalBatches ?? 3}
                  </p>
                  <p className="max-w-3xl text-sm leading-6 text-[#6B7280]">
                    Review the candidates in this set. Expand their profile to see full details and choose the one you want to keep.
                  </p>
                </div>
                <Badge className="inline-flex rounded-full bg-[#F5E7B8] px-4 py-2 text-sm font-semibold text-[#8A5A00] shadow-none">
                  2-candidate set
                </Badge>
              </div>

              <div className="grid gap-8 md:grid-cols-2">
                {currentBatch.map((candidate) => {
                  return (
                    <CandidateCard
                      key={candidate.id}
                      candidate={candidate}
                      isSelected={selectedCandidateId === candidate.id}
                      isSelecting={isAdvancing && selectedCandidateId === candidate.id}
                      selectionLocked={isAdvancing && selectedCandidateId !== candidate.id}
                      onOpenDetails={() => setActiveCandidate(candidate)}
                      onSelect={() => void handleSelect(candidate.id)}
                      showSelectButton
                    />
                  );
                })}
              </div>
            </div>
          )}

          {!isLoading && !completed && currentBatch.length === 0 && (
            <div className="rounded-[20px] border border-[#E7E0D4] bg-[#EFE6D8] p-4 text-sm text-[#6B7280]">
              Preparing the next batch. If this screen just refreshed, the session will resume from the last saved step.
            </div>
          )}

          {completed && (
            <div className="space-y-5">
              <div className="rounded-[20px] border border-[#DDF5E6] bg-[#F4FBF7] p-4 text-sm text-[#0F6B3A]">
                <p className="font-semibold">Selection complete</p>
                <p className="mt-1">The backend has analyzed your choices and reranked the full candidate pool using the signals you showed.</p>
              </div>

              {summaryLines.length > 0 && (
                <Card className="rounded-[24px] border border-[#E7E0D4] bg-[#F8F5EE] shadow-[0_8px_24px_rgba(0,0,0,0.04)]">
                  <CardHeader>
                    <CardTitle className="text-base">Preference analysis</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm text-gray-700">
                    {summaryLines.map((line) => (
                      <p key={line}>{line}</p>
                    ))}
                  </CardContent>
                </Card>
              )}

              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-gray-900">Top reranked candidates</p>
                  <Badge variant="high">{finalCandidates.length} candidates</Badge>
                </div>

                <div className="grid gap-8 md:grid-cols-2">
                  {finalCandidates.map((candidate) => {
                    return (
                      <CandidateCard
                        key={candidate.id}
                        candidate={candidate}
                        isSelected={false}
                        isSelecting={false}
                        selectionLocked={false}
                        onOpenDetails={() => setActiveCandidate(candidate)}
                        showSelectButton={false}
                      />
                    );
                  })}
                </div>
              </div>

              <Separator />

              <div className="grid gap-3 md:grid-cols-2">
                <Button className="w-full justify-center" onClick={() => router.push("/outreach")}>
                  Continue to Outreach
                </Button>
                <Button variant="outline" className="w-full justify-center" onClick={() => void refreshFinalResults()}>
                  Refresh final results
                </Button>
              </div>
            </div>
          )}

          <Modal
            open={Boolean(activeCandidate)}
            onOpenChange={(open) => {
              if (!open) {
                setActiveCandidate(null);
              }
            }}
            title={activeCandidate?.name || "Candidate profile"}
            description={
              activeCandidate ? `${activeCandidate.headline || activeCandidate.role}${activeCandidate.company ? ` @ ${activeCandidate.company}` : ""}` : ""
            }
            className="max-w-6xl"
          >
            {activeCandidate && (
              <div className="max-h-[78vh] space-y-5 overflow-y-auto pr-1">
                <CandidateDetails candidate={activeCandidate} />

                <div className="flex flex-col gap-3 md:flex-row">
                  <Button
                    className="w-full justify-center rounded-[14px] bg-[#0F6B3A] text-[16px] font-semibold text-white hover:bg-[#0C5A31] md:w-auto md:flex-1"
                    onClick={() => void handleSelect(activeCandidate.id)}
                    disabled={isAdvancing || selectedCandidateId !== "" || activeCandidate.status === "shortlisted"}
                  >
                    {isAdvancing && selectedCandidateId === activeCandidate.id ? "Saving choice..." : "Select this candidate"}
                  </Button>
                  <Button
                    variant="outline"
                    className="w-full justify-center rounded-[14px] border-[#E7E0D4] bg-white md:w-auto"
                    onClick={() => setActiveCandidate(null)}
                  >
                    Close
                  </Button>
                </div>
              </div>
            )}
          </Modal>
          <div className="mt-6 flex items-center justify-center gap-2 text-sm text-[#6B7280]">
            <ShieldCheck className="h-4 w-4 text-[#0F6B3A]" />
            <span>Your selection helps us improve future matches</span>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
