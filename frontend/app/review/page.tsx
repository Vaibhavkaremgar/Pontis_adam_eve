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

export default function ReviewPage() {
  const router = useRouter();
  const { user, isSessionReady, jobId, isRefined } = useAppContext();

  const [session, setSession] = useState<CandidateSelectionSession | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isAdvancing, setIsAdvancing] = useState(false);
  const [error, setError] = useState("");
  const [selectedCandidateId, setSelectedCandidateId] = useState("");

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

  const handleSelect = async (candidateId: string) => {
    if (!jobId || !session || isAdvancing || completed) return;
    setIsAdvancing(true);
    setError("");
    setSelectedCandidateId(candidateId);

    const result = await submitSelectionChoice({ jobId, candidateId });
    if (!result.success || !result.data) {
      setError(result.error || "Could not record candidate selection.");
      setIsAdvancing(false);
      setSelectedCandidateId("");
      return;
    }

    setSession(result.data);
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
      <Card className="mx-auto w-full max-w-[980px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Candidate selection</CardTitle>
          <CardDescription>
            Review 3 batches of 2 candidates. Select one from each pair to teach the ranking model your preference.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-6">
          {isRefined && (
            <div className="rounded-2xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
              Voice intake completed. The selection flow is now running on the refined job profile.
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-gray-600">
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

          {isLoading && <p className="text-sm text-gray-500">Loading selection session...</p>}
          {error && <p className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}

          {!isLoading && !completed && currentBatch.length > 0 && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                    Batch {session?.currentBatchIndex ? session.currentBatchIndex + 1 : 1} of {session?.totalBatches ?? 3}
                  </p>
                  <p className="text-sm text-gray-600">Select one candidate. The other candidate in this pair is tracked as rejected.</p>
                </div>
                <Badge variant="medium">2-candidate set</Badge>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                {currentBatch.map((candidate) => (
                  <Card
                    key={candidate.id}
                    className={`border transition-all ${selectedCandidateId === candidate.id ? "border-green-300 bg-green-50" : "border-[rgba(120,100,80,0.08)] bg-[#F3EDE3]"}`}
                  >
                    <CardHeader className="space-y-2 pb-3">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <CardTitle className="text-lg">{candidate.name || candidate.id.slice(0, 8)}</CardTitle>
                          <CardDescription>
                            {candidate.role}
                            {candidate.company ? ` @ ${candidate.company}` : ""}
                          </CardDescription>
                        </div>
                        <Badge variant={candidate.strategy === "HIGH" ? "high" : candidate.strategy === "MEDIUM" ? "medium" : "low"}>
                          {candidate.fitScore.toFixed(1)} / 5
                        </Badge>
                      </div>
                      <p className="text-xs text-gray-500">{statusLabel(candidate)}</p>
                    </CardHeader>

                    <CardContent className="space-y-4">
                      {candidate.skills.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {candidate.skills.slice(0, 6).map((skill) => (
                            <span key={`${candidate.id}-${skill}`} className="rounded-full bg-white/80 px-2 py-0.5 text-xs text-gray-700">
                              {skill}
                            </span>
                          ))}
                        </div>
                      )}

                      {candidate.summary && <p className="text-sm leading-relaxed text-gray-700">{candidate.summary}</p>}
                      {renderSignals(candidate)}

                      <Button
                        className="w-full justify-center"
                        onClick={() => void handleSelect(candidate.id)}
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

                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {finalCandidates.map((candidate) => (
                    <Card key={candidate.id} className="border-[rgba(120,100,80,0.08)] bg-white">
                      <CardHeader className="space-y-2 pb-3">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <CardTitle className="text-base">{candidate.name || candidate.id.slice(0, 8)}</CardTitle>
                            <CardDescription>
                              {candidate.role}
                              {candidate.company ? ` @ ${candidate.company}` : ""}
                            </CardDescription>
                          </div>
                          <Badge variant={candidate.strategy === "HIGH" ? "high" : candidate.strategy === "MEDIUM" ? "medium" : "low"}>
                            {candidate.fitScore.toFixed(1)} / 5
                          </Badge>
                        </div>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        {candidate.summary && <p className="text-sm text-gray-700">{candidate.summary}</p>}
                        <div className="flex flex-wrap gap-1.5">
                          {candidate.skills.slice(0, 5).map((skill) => (
                            <span key={`${candidate.id}-final-${skill}`} className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
                              {skill}
                            </span>
                          ))}
                        </div>
                        {candidate.explanation?.aiReasoning && (
                          <p className="text-xs italic text-gray-500">{candidate.explanation.aiReasoning}</p>
                        )}
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
        </CardContent>
      </Card>
    </AppShell>
  );
}
