"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Modal } from "@/components/ui/modal";
import { useAppContext } from "@/context/AppContext";
import { getReadyCandidates, type ReadyCandidate, type ReadyCandidateProfile } from "@/lib/api/results";
import { isSuperAdminRole } from "@/lib/roles";

const LIFECYCLE_LABELS: Record<string, string> = {
  TO_BE_ACCEPTED: "To Be Accepted",
  ACCEPTED: "Accepted",
  TO_BE_INTERVIEWED: "To Be Interviewed",
};

const LIFECYCLE_VARIANT: Record<string, "neutral" | "info" | "high"> = {
  TO_BE_ACCEPTED: "neutral",
  ACCEPTED: "info",
  TO_BE_INTERVIEWED: "high",
};

function SkillPill({ skill }: { skill: string }) {
  return (
    <span className="inline-block rounded-full border border-[rgba(120,100,80,0.15)] bg-white/70 px-2 py-0.5 text-xs text-gray-700">
      {skill}
    </span>
  );
}

function ReadyCard({
  candidate,
  onExpand,
}: {
  candidate: ReadyCandidate;
  onExpand: (c: ReadyCandidate) => void;
}) {
  const skills = (candidate.skills ?? []).slice(0, 5);
  const matchPct = candidate.match_score ? Math.round(candidate.match_score * 100) : null;

  return (
    <div className="space-y-3 rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-semibold text-gray-900">{candidate.name || "Unnamed candidate"}</p>
          {(candidate.role || candidate.company) && (
            <p className="mt-0.5 truncate text-sm text-gray-600">
              {[candidate.role, candidate.company].filter(Boolean).join(" · ")}
            </p>
          )}
          {candidate.location && (
            <p className="mt-0.5 text-xs text-gray-500">{candidate.location}</p>
          )}
        </div>
        <Badge variant={LIFECYCLE_VARIANT[candidate.lifecycle_state] ?? "neutral"}>
          {LIFECYCLE_LABELS[candidate.lifecycle_state] ?? candidate.lifecycle_state}
        </Badge>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs text-gray-600">
        {candidate.years_experience != null && candidate.years_experience > 0 && (
          <span>{candidate.years_experience.toFixed(0)} yrs exp</span>
        )}
        {matchPct != null && matchPct > 0 && (
          <span className="font-medium text-[#0F6B3A]">{matchPct}% match</span>
        )}
      </div>

      {skills.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {skills.map((s) => <SkillPill key={s} skill={s} />)}
          {(candidate.skills?.length ?? 0) > 5 && (
            <span className="text-xs text-gray-400">+{(candidate.skills?.length ?? 0) - 5} more</span>
          )}
        </div>
      )}

      {candidate.summary && (
        <p className="line-clamp-2 text-xs text-gray-600">{candidate.summary}</p>
      )}

      <Button className="w-full justify-center" variant="outline" onClick={() => onExpand(candidate)}>
        View profile
      </Button>
    </div>
  );
}

function WorkExperienceSection({ items }: { items: unknown[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#0F6B3A]">Work Experience</p>
      <div className="mt-2 space-y-2">
        {items.map((item, i) => {
          const e = item as Record<string, unknown>;
          const title = String(e.title || e.role || e.position || "");
          const company = String(e.company || e.company_name || e.employer || "");
          const dates = String(e.dates || e.duration || e.period || "");
          const desc = String(e.description || e.summary || "");
          return (
            <div key={i} className="rounded-xl bg-white/80 p-3 text-sm">
              {title && <p className="font-medium text-gray-900">{title}</p>}
              {company && <p className="text-gray-700">{company}</p>}
              {dates && <p className="text-xs text-gray-500">{dates}</p>}
              {desc && <p className="mt-1 text-xs text-gray-600 line-clamp-3">{desc}</p>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EducationSection({ items }: { items: unknown[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#0F6B3A]">Education</p>
      <div className="mt-2 space-y-2">
        {items.map((item, i) => {
          if (typeof item === "string") {
            return <p key={i} className="rounded-xl bg-white/80 p-3 text-sm text-gray-800">{item}</p>;
          }
          const e = item as Record<string, unknown>;
          const degree = String(e.degree || e.qualification || e.title || "");
          const institution = String(e.institution || e.school || e.university || "");
          const year = String(e.year || e.graduation_year || e.dates || "");
          return (
            <div key={i} className="rounded-xl bg-white/80 p-3 text-sm">
              {degree && <p className="font-medium text-gray-900">{degree}</p>}
              {institution && <p className="text-gray-700">{institution}</p>}
              {year && <p className="text-xs text-gray-500">{year}</p>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CertificationsSection({ items }: { items: unknown[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#0F6B3A]">Certifications</p>
      <div className="mt-2 space-y-1">
        {items.map((item, i) => {
          if (typeof item === "string") {
            return <p key={i} className="rounded-xl bg-white/80 px-3 py-2 text-sm text-gray-800">{item}</p>;
          }
          const c = item as Record<string, unknown>;
          const name = String(c.name || c.title || "");
          const issuer = String(c.issuer || "");
          const date = String(c.issued_date || c.expiry_date || "");
          return (
            <div key={i} className="rounded-xl bg-white/80 px-3 py-2 text-sm">
              {name && <p className="font-medium text-gray-900">{name}</p>}
              {issuer && <p className="text-xs text-gray-600">{issuer}</p>}
              {date && <p className="text-xs text-gray-500">{date}</p>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ExpandedProfile({ candidate, profile }: { candidate: ReadyCandidate; profile: ReadyCandidateProfile }) {
  const matchPct = profile.match_score ? Math.round(profile.match_score * 100) : null;
  const semanticPct = profile.semantic_match ? Math.round(profile.semantic_match * 100) : null;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F8F5EE] p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">{profile.name}</h3>
            {(profile.role || profile.company) && (
              <p className="mt-0.5 text-sm text-gray-700">
                {[profile.role, profile.company].filter(Boolean).join(" · ")}
              </p>
            )}
            {profile.location && <p className="mt-0.5 text-xs text-gray-500">{profile.location}</p>}
            {profile.years_experience != null && profile.years_experience > 0 && (
              <p className="mt-0.5 text-xs text-gray-500">{profile.years_experience.toFixed(0)} years experience</p>
            )}
          </div>
          <Badge variant={LIFECYCLE_VARIANT[candidate.lifecycle_state] ?? "neutral"}>
            {LIFECYCLE_LABELS[candidate.lifecycle_state] ?? candidate.lifecycle_state}
          </Badge>
        </div>
      </div>

      {/* Summary */}
      {profile.summary && (
        <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F8F5EE] p-4">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#0F6B3A]">Professional Summary</p>
          <p className="mt-2 text-sm text-gray-700">{profile.summary}</p>
        </div>
      )}

      {/* Match info */}
      {(matchPct != null || (profile.matched_requirements?.length ?? 0) > 0 || (profile.missing_requirements?.length ?? 0) > 0) && (
        <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F8F5EE] p-4 space-y-3">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#0F6B3A]">Match Evaluation</p>
          <div className="flex flex-wrap gap-4 text-sm">
            {matchPct != null && (
              <div><p className="text-xs text-gray-500">Match Score</p><p className="font-semibold text-gray-900">{matchPct}%</p></div>
            )}
            {semanticPct != null && (
              <div><p className="text-xs text-gray-500">Semantic Match</p><p className="font-semibold text-gray-900">{semanticPct}%</p></div>
            )}
          </div>
          {(profile.matched_requirements?.length ?? 0) > 0 && (
            <div>
              <p className="text-xs text-gray-500 mb-1">Matched Requirements</p>
              <div className="flex flex-wrap gap-1">
                {profile.matched_requirements!.map((r) => (
                  <span key={r} className="rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-800">{r}</span>
                ))}
              </div>
            </div>
          )}
          {(profile.missing_requirements?.length ?? 0) > 0 && (
            <div>
              <p className="text-xs text-gray-500 mb-1">Missing Requirements</p>
              <div className="flex flex-wrap gap-1">
                {profile.missing_requirements!.map((r) => (
                  <span key={r} className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">{r}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Skills */}
      {(profile.skills?.length ?? 0) > 0 && (
        <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F8F5EE] p-4">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#0F6B3A]">Skills</p>
          <div className="mt-2 flex flex-wrap gap-1">
            {profile.skills!.map((s) => <SkillPill key={s} skill={s} />)}
          </div>
        </div>
      )}

      {/* Work Experience */}
      {(profile.work_experience?.length ?? 0) > 0 && (
        <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F8F5EE] p-4">
          <WorkExperienceSection items={profile.work_experience!} />
        </div>
      )}

      {/* Education */}
      {(profile.education?.length ?? 0) > 0 && (
        <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F8F5EE] p-4">
          <EducationSection items={profile.education!} />
        </div>
      )}

      {/* Certifications */}
      {(profile.certifications?.length ?? 0) > 0 && (
        <div className="rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F8F5EE] p-4">
          <CertificationsSection items={profile.certifications!} />
        </div>
      )}
    </div>
  );
}

function ReadySection({
  title,
  candidates,
  onExpand,
}: {
  title: string;
  candidates: ReadyCandidate[];
  onExpand: (c: ReadyCandidate) => void;
}) {
  if (!candidates.length) return null;
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-gray-700">{title}</p>
        <Badge variant="neutral">{candidates.length}</Badge>
      </div>
      {candidates.map((c) => (
        <ReadyCard key={c.candidate_id} candidate={c} onExpand={onExpand} />
      ))}
    </div>
  );
}

function ReadyPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, isSessionReady, jobId, setJobId } = useAppContext();
  const queryJobId = String(searchParams.get("jobId") || "").trim();
  const effectiveJobId = jobId || queryJobId;

  const [toBeAccepted, setToBeAccepted] = useState<ReadyCandidate[]>([]);
  const [accepted, setAccepted] = useState<ReadyCandidate[]>([]);
  const [toBeInterviewed, setToBeInterviewed] = useState<ReadyCandidate[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeCandidate, setActiveCandidate] = useState<ReadyCandidate | null>(null);

  const totalCount = toBeAccepted.length + accepted.length + toBeInterviewed.length;

  const loadReady = async () => {
    if (!effectiveJobId || !user) return;
    setIsLoading(true);
    setError("");
    const result = await getReadyCandidates(effectiveJobId);
    if (!result.success || !result.data) {
      setError(result.error || "Could not load ready candidates.");
    } else {
      setToBeAccepted(result.data.ready.toBeAccepted ?? []);
      setAccepted(result.data.ready.accepted ?? []);
      setToBeInterviewed(result.data.ready.toBeInterviewed ?? []);
    }
    setIsLoading(false);
  };

  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) { router.replace("/login"); return; }
    if (isSuperAdminRole(user.role)) { router.replace("/admin"); return; }
    if (!effectiveJobId) { router.replace("/job"); return; }
    void loadReady();
  }, [effectiveJobId, isSessionReady, user]);

  useEffect(() => {
    if (jobId || !queryJobId) return;
    setJobId(queryJobId);
  }, [jobId, queryJobId, setJobId]);

  const activeProfile = activeCandidate?.profile ?? null;

  return (
    <AppShell activeStep={5}>
      <Card className="mx-auto w-full max-w-[760px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Ready candidates</CardTitle>
          <CardDescription>
            Candidates awaiting acceptance, accepted, or scheduled for interview.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-6">
          {isLoading && <p className="text-sm text-gray-600">Loading candidates...</p>}
          {error && <p className="text-sm text-red-600">{error}</p>}

          {!isLoading && !error && totalCount === 0 && (
            <div className="rounded-xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4 text-sm text-gray-600">
              No ready candidates yet.
            </div>
          )}

          <ReadySection
            title="To Be Accepted"
            candidates={toBeAccepted}
            onExpand={setActiveCandidate}
          />
          <ReadySection
            title="Accepted"
            candidates={accepted}
            onExpand={setActiveCandidate}
          />
          <ReadySection
            title="To Be Interviewed"
            candidates={toBeInterviewed}
            onExpand={setActiveCandidate}
          />

          <Button
            variant="outline"
            className="w-full justify-center"
            onClick={() => router.push(effectiveJobId ? `/results?jobId=${encodeURIComponent(effectiveJobId)}` : "/results")}
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
        onOpenChange={(open) => { if (!open) setActiveCandidate(null); }}
        title={activeCandidate ? `${activeCandidate.name || "Candidate"} — Profile` : "Candidate Profile"}
        description="Recruiter-safe candidate profile."
        className="max-w-2xl"
      >
        <div className="max-h-[78vh] overflow-y-auto pr-1">
          {activeCandidate && activeProfile ? (
            <ExpandedProfile candidate={activeCandidate} profile={activeProfile} />
          ) : activeCandidate ? (
            <div className="space-y-3 rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F8F5EE] p-4">
              <p className="font-semibold text-gray-900">{activeCandidate.name}</p>
              {(activeCandidate.role || activeCandidate.company) && (
                <p className="text-sm text-gray-700">{[activeCandidate.role, activeCandidate.company].filter(Boolean).join(" · ")}</p>
              )}
              {activeCandidate.location && <p className="text-xs text-gray-500">{activeCandidate.location}</p>}
              {activeCandidate.summary && <p className="text-sm text-gray-600">{activeCandidate.summary}</p>}
              {(activeCandidate.skills?.length ?? 0) > 0 && (
                <div className="flex flex-wrap gap-1">
                  {activeCandidate.skills!.map((s) => <SkillPill key={s} skill={s} />)}
                </div>
              )}
            </div>
          ) : null}
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
