"use client";

/**
 * What this file does:
 * Runs the new 3-step candidate selection flow.
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

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Modal } from "@/components/ui/modal";
import { Separator } from "@/components/ui/separator";
import { useAppContext } from "@/context/AppContext";
import {
  getFinalSelectionResults,
  getFirstSelectionBatch,
  submitSelectionChoice,
} from "@/lib/api/candidates";
import type { Candidate, CandidateSelectionAnalysis, CandidateSelectionSession } from "@/types";

function statusLabel(candidate: Candidate): string {
  if (candidate.status === "shortlisted") return "Selected";
  if (candidate.status === "rejected") return "Rejected";
  return "Awaiting choice";
}

function renderSignals(candidate: Candidate) {
  const explanation = candidate.explanation;
  const penalties = explanation?.penalties ?? {};
  const semantic = explanation?.semanticScore ?? explanation?.semantic ?? 0;
  const matchedSkills = explanation?.skillsMatched ?? explanation?.skills_match ?? [];
  const experienceMatch = explanation?.experienceMatch || explanation?.candidateExperience || explanation?.jobExperience || "";

  return (
    <div className="space-y-1 rounded-xl bg-white/70 p-3 text-xs text-gray-600">
      <p>Semantic: <span className="font-medium text-gray-800">{(semantic * 100).toFixed(0)}%</span></p>
      {experienceMatch && <p>Experience: <span className="font-medium text-gray-800">{experienceMatch}</span></p>}
      {matchedSkills.length > 0 && (
        <p>Matched skills: <span className="font-medium text-gray-800">{matchedSkills.slice(0, 4).join(", ")}</span></p>
      )}
      {typeof penalties.selectionPreferenceBonus === "number" && (
        <p>Selection boost: <span className="font-medium text-green-700">+{penalties.selectionPreferenceBonus.toFixed(3)}</span></p>
      )}
      {explanation?.aiReasoning && <p className="italic text-gray-500">{explanation.aiReasoning}</p>}
    </div>
  );
}

function analysisSummary(analysis: CandidateSelectionAnalysis | null | undefined) {
  if (!analysis) return [];
  return [
    analysis.summary,
    analysis.preferenceSignals.sharedSkills.length > 0
      ? `Shared skills: ${analysis.preferenceSignals.sharedSkills.slice(0, 5).join(", ")}`
      : "",
    analysis.preferenceSignals.sharedRoles.length > 0
      ? `Role alignment: ${analysis.preferenceSignals.sharedRoles.slice(0, 5).join(", ")}`
      : "",
    analysis.preferenceSignals.sharedCompanies.length > 0
      ? `Company overlap: ${analysis.preferenceSignals.sharedCompanies.slice(0, 5).join(", ")}`
      : "",
  ].filter(Boolean);
}

function intentSummaryLines(session: CandidateSelectionSession | null) {
  if (!session?.intentProfile) return [];
  const profile = session.intentProfile;
  return [
    profile.required_skills?.length ? `Required skills: ${profile.required_skills.slice(0, 5).join(", ")}` : "",
    profile.preferred_skills?.length ? `Preferred skills: ${profile.preferred_skills.slice(0, 5).join(", ")}` : "",
    profile.culture_preferences?.length ? `Culture: ${profile.culture_preferences.slice(0, 4).join(", ")}` : "",
    typeof profile.startup_weight === "number" ? `Startup weight: ${(profile.startup_weight * 100).toFixed(0)}%` : "",
    typeof profile.domain_weight === "number" ? `Domain weight: ${(profile.domain_weight * 100).toFixed(0)}%` : "",
    typeof profile.leadership_weight === "number" ? `Leadership weight: ${(profile.leadership_weight * 100).toFixed(0)}%` : "",
    typeof profile.infra_weight === "number" ? `Infra weight: ${(profile.infra_weight * 100).toFixed(0)}%` : "",
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
      return;
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
  const intentLines = useMemo(() => intentSummaryLines(session), [session]);
  const currentPair = session?.currentPair ?? null;

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
    } else if (result.error) {
      setError(result.error);
    }
    setIsLoading(false);
  };

  return (
    <AppShell activeStep={4}>
      <Card className="mx-auto w-full max-w-[1440px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Candidate selection</CardTitle>
          <CardDescription>
            Review 3 batches of 2 candidates. Select one from each pair to teach the ranking model your preference.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-8 px-4 pb-6 md:px-6">
          {isRefined && (
            <div className="rounded-2xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
              Voice intake completed. The selection flow is now running on the refined job profile.
            </div>
          )}

          <div className="grid gap-3 rounded-2xl bg-[#FAF7F1] p-4 text-sm text-gray-600 md:grid-cols-3">
            <span>
              Progress: <strong>{session ? `${progress} / ${session.totalBatches}` : "0 / 3"}</strong>
            </span>
            <span>
              Selected: <strong>{session?.selectedCandidateIds.length ?? 0}</strong>
            </span>
            <span>
              Rejected: <strong>{session?.rejectedCandidateIds.length ?? 0}</strong>
            </span>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
            {intentLines.length > 0 && (
              <Card className="border-[rgba(120,100,80,0.08)] bg-[#F8F5EF]">
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">Recruiter intent summary</CardTitle>
                  <CardDescription>The AI has normalized what you care about before the final shortlist rerun.</CardDescription>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2 text-xs text-gray-700">
                  {intentLines.map((line) => (
                    <span key={line} className="rounded-full bg-white px-3 py-1">
                      {line}
                    </span>
                  ))}
                </CardContent>
              </Card>
            )}

            {currentPair?.rationale && (
              <Card className="border-[rgba(120,100,80,0.08)] bg-white">
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">Why this pair</CardTitle>
                  <CardDescription>{currentPair.rationale}</CardDescription>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2 text-xs text-gray-600">
                  {(currentPair.contrast_axes || []).map((axis) => (
                    <span key={axis} className="rounded-full bg-[#F3EDE3] px-3 py-1">
                      {axis.replace(/_/g, " ")}
                    </span>
                  ))}
                  {typeof currentPair.signal_quality === "number" && (
                    <span className="rounded-full bg-green-50 px-3 py-1 text-green-700">
                      Signal quality {(currentPair.signal_quality * 100).toFixed(0)}%
                    </span>
                  )}
                </CardContent>
              </Card>
            )}
          </div>

          {isLoading && <p className="text-sm text-gray-500">Loading selection session...</p>}
          {error && <p className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
          {selectionDebug && (
            <details className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <summary className="cursor-pointer font-medium">Debug details</summary>
              <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs leading-relaxed text-amber-950">
                {selectionDebug}
              </pre>
            </details>
          )}

          {!isLoading && !completed && currentBatch.length > 0 && (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                    Batch {session?.currentBatchIndex ? session.currentBatchIndex + 1 : 1} of {session?.totalBatches ?? 3}
                  </p>
                  <p className="text-sm text-gray-600">Select one candidate. The other candidate in this pair is tracked as rejected.</p>
                </div>
                <Badge variant="medium">2-candidate set</Badge>
              </div>

              <div className="grid gap-5 xl:grid-cols-2">
                {currentBatch.map((candidate) => (
                  <Card
                    key={candidate.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => setActiveCandidate(candidate)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setActiveCandidate(candidate);
                      }
                    }}
                    className={`h-full cursor-pointer border transition-all hover:-translate-y-0.5 hover:shadow-[0_10px_24px_rgba(0,0,0,0.08)] ${selectedCandidateId === candidate.id ? "border-green-300 bg-green-50" : "border-[rgba(120,100,80,0.08)] bg-[#F3EDE3]"}`}
                  >
                    <CardHeader className="space-y-3 pb-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <CardTitle className="text-xl">{candidate.name || candidate.id.slice(0, 8)}</CardTitle>
                          <CardDescription>
                            {candidate.headline || candidate.role}
                            {candidate.company ? ` @ ${candidate.company}` : ""}
                            {candidate.location ? ` • ${candidate.location}` : ""}
                          </CardDescription>
                        </div>
                        <Badge variant={candidate.strategy === "HIGH" ? "high" : candidate.strategy === "MEDIUM" ? "medium" : "low"}>
                          {candidate.fitScore.toFixed(1)} / 5
                        </Badge>
                      </div>
                      <p className="text-xs text-gray-500">{statusLabel(candidate)}</p>
                    </CardHeader>

                    <CardContent className="space-y-5">
                      <div className="space-y-2 rounded-2xl bg-white/75 p-4 text-sm text-gray-700">
                        <p>
                          Email: <span className="font-medium text-gray-900">{candidate.email || "Not provided"}</span>
                        </p>
                        <p>
                          Experience: <span className="font-medium text-gray-900">{candidate.yearsExperience ? `${candidate.yearsExperience.toFixed(1)} years` : "Not provided"}</span>
                        </p>
                      </div>

                      {candidate.skills.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {candidate.skills.slice(0, 3).map((skill) => (
                            <span key={`${candidate.id}-${skill}`} className="rounded-full bg-white/90 px-3 py-1 text-xs text-gray-700">
                              {skill}
                            </span>
                          ))}
                        </div>
                      )}

                      {candidate.summary && <p className="text-[15px] leading-relaxed text-gray-700">{trimText(candidate.summary, 180)}</p>}
                      {renderSignals(candidate)}

                      <Button
                        className="w-full justify-center"
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleSelect(candidate.id);
                        }}
                        disabled={isAdvancing || Boolean(selectedCandidateId) && selectedCandidateId !== candidate.id}
                      >
                        {isAdvancing && selectedCandidateId === candidate.id ? "Saving choice..." : "Select this candidate"}
                      </Button>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {!isLoading && !completed && currentBatch.length === 0 && (
            <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4 text-sm text-gray-600">
              Preparing the next batch. If this screen just refreshed, the session will resume from the last saved step.
            </div>
          )}

          {completed && (
            <div className="space-y-5">
              <div className="rounded-2xl border border-green-200 bg-green-50 p-4 text-sm text-green-900">
                <p className="font-semibold">Selection complete</p>
                <p className="mt-1">The backend has analyzed your choices and reranked the full candidate pool using the signals you showed.</p>
              </div>

              {summaryLines.length > 0 && (
                <Card className="border-[rgba(120,100,80,0.08)] bg-[#F3EDE3]">
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

                <div className="grid gap-5 lg:grid-cols-2 xl:grid-cols-3">
                  {finalCandidates.map((candidate) => (
                    <Card
                      key={candidate.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => setActiveCandidate(candidate)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          setActiveCandidate(candidate);
                        }
                      }}
                      className="h-full cursor-pointer border-[rgba(120,100,80,0.08)] bg-white transition-all hover:-translate-y-0.5 hover:shadow-[0_10px_24px_rgba(0,0,0,0.08)]"
                    >
                      <CardHeader className="space-y-3 pb-4">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <CardTitle className="text-lg">{candidate.name || candidate.id.slice(0, 8)}</CardTitle>
                            <CardDescription>
                              {candidate.headline || candidate.role}
                              {candidate.company ? ` @ ${candidate.company}` : ""}
                              {candidate.location ? ` • ${candidate.location}` : ""}
                            </CardDescription>
                          </div>
                          <Badge variant={candidate.strategy === "HIGH" ? "high" : candidate.strategy === "MEDIUM" ? "medium" : "low"}>
                            {candidate.fitScore.toFixed(1)} / 5
                          </Badge>
                        </div>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        <div className="space-y-2 rounded-2xl bg-[#F8FAFC] p-4 text-sm text-gray-600">
                          <p>
                            Email: <span className="font-medium text-gray-800">{candidate.email || "Not provided"}</span>
                          </p>
                          <p>
                            Experience: <span className="font-medium text-gray-800">{candidate.yearsExperience ? `${candidate.yearsExperience.toFixed(1)} years` : "Not provided"}</span>
                          </p>
                        </div>
                        {candidate.summary && <p className="text-[15px] text-gray-700">{trimText(candidate.summary, 160)}</p>}
                        <div className="flex flex-wrap gap-2">
                          {candidate.skills.slice(0, 3).map((skill) => (
                            <span key={`${candidate.id}-final-${skill}`} className="rounded-full bg-gray-100 px-3 py-1 text-xs text-gray-700">
                              {skill}
                            </span>
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  ))}
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
              activeCandidate
                ? `${activeCandidate.headline || activeCandidate.role}${activeCandidate.company ? ` @ ${activeCandidate.company}` : ""}`
                : ""
            }
            className="max-w-4xl"
          >
            {activeCandidate && (
              <div className="max-h-[78vh] space-y-5 overflow-y-auto pr-1">
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-xl bg-white/80 p-4 text-sm text-gray-700">
                    <p className="text-xs uppercase tracking-wide text-gray-500">Profile</p>
                    <div className="mt-2 space-y-1">
                      <p>
                        Role: <span className="font-medium text-gray-900">{activeCandidate.headline || activeCandidate.role || "Not provided"}</span>
                      </p>
                      <p>
                        Company: <span className="font-medium text-gray-900">{activeCandidate.company || "Not provided"}</span>
                      </p>
                      <p>
                        Location: <span className="font-medium text-gray-900">{activeCandidate.location || "Not provided"}</span>
                      </p>
                      <p>
                        Experience: <span className="font-medium text-gray-900">{activeCandidate.yearsExperience ? `${activeCandidate.yearsExperience.toFixed(1)} years` : "Not provided"}</span>
                      </p>
                      <p>
                        Email: <span className="font-medium text-gray-900">{activeCandidate.email || "Not provided"}</span>
                      </p>
                    </div>
                  </div>
                  <div className="rounded-xl bg-white/80 p-4 text-sm text-gray-700">
                    <p className="text-xs uppercase tracking-wide text-gray-500">Scoring</p>
                    <div className="mt-2 space-y-1">
                      <p>
                        Fit score: <span className="font-medium text-gray-900">{activeCandidate.fitScore.toFixed(1)} / 5</span>
                      </p>
                      <p>
                        Status: <span className="font-medium text-gray-900">{statusLabel(activeCandidate)}</span>
                      </p>
                      <p>
                        Resume included: <span className="font-medium text-gray-900">{activeCandidate.resumeText ? "Yes" : "No"}</span>
                      </p>
                      <p>
                        Mock email: <span className="font-medium text-gray-900">{activeCandidate.isMockEmail ? "Yes" : "No"}</span>
                      </p>
                    </div>
                  </div>
                </div>

                <div className="space-y-2 rounded-xl bg-white/80 p-4">
                  <p className="text-xs uppercase tracking-wide text-gray-500">Skills</p>
                  <div className="flex flex-wrap gap-2">
                    {formatList(activeCandidate.skills).map((skill) => (
                      <span key={`modal-skill-${activeCandidate.id}-${skill}`} className="rounded-full bg-[#F3EDE3] px-3 py-1 text-xs text-gray-700">
                        {skill}
                      </span>
                    ))}
                  </div>
                </div>

                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-xl bg-white/80 p-4 text-sm text-gray-700">
                    <p className="text-xs uppercase tracking-wide text-gray-500">Education</p>
                    <ul className="mt-2 space-y-1">
                      {formatList(activeCandidate.education).map((item) => (
                        <li key={`education-${activeCandidate.id}-${item}`}>{item}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="rounded-xl bg-white/80 p-4 text-sm text-gray-700">
                    <p className="text-xs uppercase tracking-wide text-gray-500">Projects</p>
                    <ul className="mt-2 space-y-1">
                      {formatList(activeCandidate.projects).map((item) => (
                        <li key={`project-${activeCandidate.id}-${item}`}>{item}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="rounded-xl bg-white/80 p-4 text-sm text-gray-700">
                    <p className="text-xs uppercase tracking-wide text-gray-500">Certifications</p>
                    <ul className="mt-2 space-y-1">
                      {formatList(activeCandidate.certifications).map((item) => (
                        <li key={`cert-${activeCandidate.id}-${item}`}>{item}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="rounded-xl bg-white/80 p-4 text-sm text-gray-700">
                    <p className="text-xs uppercase tracking-wide text-gray-500">Companies</p>
                    <ul className="mt-2 space-y-1">
                      {formatList(activeCandidate.companiesHistory).map((item) => (
                        <li key={`company-${activeCandidate.id}-${item}`}>{item}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="rounded-xl bg-white/80 p-4 text-sm text-gray-700 md:col-span-2">
                    <p className="text-xs uppercase tracking-wide text-gray-500">Domain experience</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {formatList(activeCandidate.domainExperience).map((item) => (
                        <span key={`domain-${activeCandidate.id}-${item}`} className="rounded-full bg-gray-100 px-3 py-1 text-xs text-gray-700">
                          {item}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>

                {activeCandidate.summary && (
                  <div className="rounded-xl bg-[#FAF7F1] p-4 text-sm text-gray-700">
                    <p className="text-xs uppercase tracking-wide text-gray-500">Summary</p>
                    <p className="mt-2 leading-relaxed">{activeCandidate.summary}</p>
                  </div>
                )}

                {activeCandidate.resumeText && (
                  <div className="rounded-xl bg-[#111111] p-4 text-sm text-white">
                    <p className="text-xs uppercase tracking-wide text-white/70">Resume text</p>
                    <pre className="mt-2 whitespace-pre-wrap font-body leading-relaxed text-white/90">
                      {activeCandidate.resumeText}
                    </pre>
                  </div>
                )}

                {activeCandidate.explanation?.sourceBreakdown && (
                  <div className="grid gap-2 rounded-xl bg-white/80 p-4 text-xs text-gray-600 sm:grid-cols-2">
                    <span>Vector: {(activeCandidate.explanation.sourceBreakdown.vector ?? 0).toFixed(2)}</span>
                    <span>Lexical: {(activeCandidate.explanation.sourceBreakdown.lexical ?? 0).toFixed(2)}</span>
                    <span>Recruiter: {(activeCandidate.explanation.sourceBreakdown.recruiterPreference ?? 0).toFixed(2)}</span>
                    <span>Selection: {(activeCandidate.explanation.sourceBreakdown.selectionRound ?? 0).toFixed(2)}</span>
                    <span>Voice: {(activeCandidate.explanation.sourceBreakdown.voiceInterview ?? 0).toFixed(2)}</span>
                    <span>Freshness: {(activeCandidate.explanation.sourceBreakdown.freshness ?? 0).toFixed(2)}</span>
                  </div>
                )}
              </div>
            )}
          </Modal>
        </CardContent>
      </Card>
    </AppShell>
  );
}
