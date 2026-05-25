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
 * Voice intake -> selection session -> recruiter preference learning -> refined shortlist -> automation handoff
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  BriefcaseBusiness,
  Building2,
  CheckCircle2,
  ChevronDown,
  CircleUserRound,
  CircleX,
  FileText,
  GraduationCap,
  MapPin,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Modal } from "@/components/ui/modal";
import { Separator } from "@/components/ui/separator";
import { useAppContext } from "@/context/AppContext";
import { getCandidateAtsTimeline, getJobAtsNotifications } from "@/lib/api/ats";
import { getFinalSelectionResults, getFirstSelectionBatch, submitSelectionChoice, swipeCandidate } from "@/lib/api/candidates";
import { sendOutreach } from "@/lib/api/outreach";
import { chooseRecruiterCalibrationArchetype, getRecruiterIntelligence } from "@/lib/api/recruiter-intelligence";
import { getInterviewInsights, submitInterviewDecision } from "@/lib/api/interviews";
import { storeShortlistedCandidateIds, storeShortlistedCandidates } from "@/lib/session";
import type { Candidate, CandidateSelectionAnalysis, CandidateSelectionSession } from "@/types";
import type { RecruiterIntelligenceSession } from "@/lib/api/recruiter-intelligence";

function statusLabel(candidate: Candidate): string {
  if (candidate.status === "shortlisted") return "Selected";
  if (candidate.status === "enriched") return "Enriched";
  if (candidate.status === "outreach_sent") return "Outreach sent";
  if (candidate.status === "rejected") return "Rejected";
  return "Awaiting choice";
}

function atsStatusLabel(candidate: Candidate): string {
  return (candidate.ats_status || candidate.status || "review_pending").replace(/_/g, " ");
}

function candidateSubtitle(candidate: Candidate): string {
  return [candidate.headline || candidate.role || "", candidate.company ? candidate.company : "", candidate.location || ""]
    .filter(Boolean)
    .join(" - ");
}

function getSourcingConfidence(candidate: Candidate): number {
  const explanation = candidate.explanation;
  const semantic = explanation?.semanticScore ?? explanation?.semantic ?? 0;
  const finalScore = explanation?.finalScore ?? candidate.fitScore;
  const recruiterPreference = explanation?.sourceBreakdown?.recruiterPreference ?? 0;
  const selectionRound = explanation?.sourceBreakdown?.selectionRound ?? explanation?.selectionRoundInfluence ?? 0;
  const freshness = explanation?.sourceBreakdown?.freshness ?? explanation?.freshnessInfluence ?? 0;

  const blended =
    semantic * 0.4 +
    (finalScore / 5) * 0.35 +
    recruiterPreference * 0.15 +
    selectionRound * 0.05 +
    freshness * 0.05;

  return Math.max(0, Math.min(1, blended));
}

function renderSignals(candidate: Candidate) {
  const explanation = candidate.explanation;
  const penalties = explanation?.penalties ?? {};
  const semantic = explanation?.semanticScore ?? explanation?.semantic ?? 0;
  const matchedSkills = explanation?.skillsMatched ?? explanation?.skills_match ?? [];
  const experienceMatch = explanation?.experienceMatch || explanation?.candidateExperience || explanation?.jobExperience || "";
  const sourcingConfidence = getSourcingConfidence(candidate);

  return (
    <div className="space-y-1 rounded-xl bg-white/70 p-3 text-xs text-gray-600">
      <p>
        Semantic: <span className="font-medium text-gray-800">{(semantic * 100).toFixed(0)}%</span>
      </p>
      <p>
        Sourcing confidence: <span className="font-medium text-gray-800">{(sourcingConfidence * 100).toFixed(0)}%</span>
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

function humanizeContrastAxis(axis: string): string {
  const value = axis.replace(/_/g, " ").trim();
  switch (axis) {
    case "startup":
      return "Startup vs enterprise";
    case "backend_infra":
      return "Backend vs infra";
    case "domain_systems":
      return "Domain vs systems";
    case "leadership_ic":
      return "Leadership vs hands-on";
    case "exact_adjacent":
      return "Near-match adjacency";
    default:
      return value.charAt(0).toUpperCase() + value.slice(1);
  }
}

function getPairAxes(session: CandidateSelectionSession | null | undefined): string[] {
  if (!session) return [];
  const rawAxes = session.currentPair?.contrast_axes ?? (session.pairExplanation as { contrast_axes?: unknown } | undefined)?.contrast_axes;
  return Array.isArray(rawAxes) ? rawAxes.filter((axis): axis is string => typeof axis === "string" && axis.trim().length > 0) : [];
}

function getPairRationale(session: CandidateSelectionSession | null | undefined): string {
  if (!session) return "";
  const explanation = (session.pairExplanation ?? session.currentPair?.pair_explanation ?? {}) as Record<string, unknown>;
  return (
    session.currentPair?.rationale ||
    String(explanation.why_selected ?? explanation.summary ?? "").trim() ||
    "This pair is chosen to expose different recruiter preferences."
  );
}

function calibrationValueList(value: unknown): string[] {
  const collected: string[] = [];

  const visit = (item: unknown) => {
    if (item == null) return;
    if (Array.isArray(item)) {
      item.forEach(visit);
      return;
    }
    if (typeof item === "string") {
      const cleaned = item.trim();
      if (!cleaned) return;
      if (/[;,|]/.test(cleaned)) {
        cleaned
          .replace(/;/g, ",")
          .replace(/\|/g, ",")
          .split(",")
          .map((part) => part.trim())
          .filter(Boolean)
          .forEach((part) => collected.push(part));
        return;
      }
      collected.push(cleaned);
      return;
    }
    if (typeof item === "number" || typeof item === "boolean") {
      collected.push(String(item));
      return;
    }
    if (typeof item === "object") {
      const record = item as Record<string, unknown>;
      for (const key of ["text", "label", "title", "name", "role", "value", "skill", "strength", "signal", "tradeoff"]) {
        if (record[key] != null) {
          visit(record[key]);
          return;
        }
      }
      Object.values(record).forEach(visit);
    }
  };

  visit(value);

  const unique: string[] = [];
  const seen = new Set<string>();
  for (const item of collected) {
    const normalized = item.trim();
    if (!normalized) continue;
    const key = normalized.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(normalized);
  }
  return unique;
}

function calibrationText(value: unknown, fallback = ""): string {
  const values = calibrationValueList(value);
  if (values.length > 0) return values.join(", ");
  if (typeof value === "string" && value.trim()) return value.trim();
  return fallback;
}

function getCalibrationCurrentArchetypes(calibration: RecruiterIntelligenceSession["calibration"] | null | undefined): Array<Record<string, unknown>> {
  const currentPair = (calibration?.current_pair ?? {}) as Record<string, unknown>;
  const archetypes = currentPair.archetypes;
  return Array.isArray(archetypes) ? archetypes.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object")) : [];
}

function getCalibrationCurrentSetId(calibration: RecruiterIntelligenceSession["calibration"] | null | undefined): string {
  if (!calibration) return "";
  const currentPair = (calibration.current_pair ?? {}) as Record<string, unknown>;
  return calibrationText(
    calibration.current_calibration_set_id ||
      currentPair.calibration_set_id ||
      currentPair.calibrationSetId ||
      ""
  );
}

function getCalibrationRoundLabel(calibration: RecruiterIntelligenceSession["calibration"] | null | undefined): string {
  if (!calibration) return "1 / 3";
  const current = Number(calibration.current_round_index || 1);
  const total = Array.isArray(calibration.archetype_sets) ? calibration.archetype_sets.length || 3 : 3;
  return `${current} / ${total}`;
}

const STARTUP_TERMS = ["startup", "seed", "series a", "series b", "early-stage", "fast-paced", "scrappy"];
const ENTERPRISE_TERMS = ["enterprise", "scale", "regulated", "large-scale", "global", "mature"];
const BACKEND_TERMS = ["backend", "api", "service", "distributed", "microservice", "python", "java", "go", "node"];
const INFRA_TERMS = ["infra", "platform", "aws", "gcp", "azure", "kubernetes", "terraform", "devops", "sre"];
const DOMAIN_TERMS = ["fintech", "healthcare", "security", "payments", "search", "ads", "commerce", "ml", "data"];
const SYSTEMS_TERMS = ["systems", "architecture", "scalable", "distributed", "performance", "reliability", "latency"];
const LEADERSHIP_TERMS = ["lead", "leadership", "mentor", "architect", "own", "ownership", "drive"];
const IC_TERMS = ["individual contributor", "ic", "hands-on", "build", "ship", "implement"];

function normalizeContrastText(value?: string): string {
  return (value || "").toLowerCase().replace(/\s+/g, " ").trim();
}

function candidateContrastText(candidate: Candidate): string {
  return normalizeContrastText(
    [
      candidate.name,
      candidate.headline,
      candidate.role,
      candidate.company,
      candidate.location,
      candidate.summary,
      ...(candidate.skills || []),
    ]
      .filter(Boolean)
      .join(" ")
  );
}

function featureScore(text: string, terms: string[]): number {
  if (!text || terms.length === 0) return 0;
  let hits = 0;
  for (const term of terms) {
    if (text.includes(term)) hits += 1;
  }
  return hits / terms.length;
}

function axisPairScore(candidate: Candidate, axis: string): [number, number] {
  const text = candidateContrastText(candidate);
  switch (axis) {
    case "startup":
      return [featureScore(text, STARTUP_TERMS), featureScore(text, ENTERPRISE_TERMS)];
    case "backend_infra":
      return [featureScore(text, BACKEND_TERMS), featureScore(text, INFRA_TERMS)];
    case "domain_systems":
      return [featureScore(text, DOMAIN_TERMS), featureScore(text, SYSTEMS_TERMS)];
    case "leadership_ic":
      return [featureScore(text, LEADERSHIP_TERMS), featureScore(text, IC_TERMS)];
    default:
      return [0, 0];
  }
}

function axisSideLabel(axis: string, isPositiveSide: boolean): string {
  switch (axis) {
    case "startup":
      return isPositiveSide ? "Startup-leaning" : "Enterprise-leaning";
    case "backend_infra":
      return isPositiveSide ? "Backend-leaning" : "Infra-leaning";
    case "domain_systems":
      return isPositiveSide ? "Domain-leaning" : "Systems-leaning";
    case "leadership_ic":
      return isPositiveSide ? "Leadership-leaning" : "Hands-on";
    default:
      return isPositiveSide ? "Higher on this axis" : "Lower on this axis";
  }
}

function getPairContrastLabels(candidate: Candidate, peer: Candidate | undefined, pairAxes: string[]): string[] {
  if (!peer) return [];
  return pairAxes
    .map((axis) => {
      const [candidatePositive, candidateNegative] = axisPairScore(candidate, axis);
      const [peerPositive, peerNegative] = axisPairScore(peer, axis);
      const candidateScore = candidatePositive - candidateNegative;
      const peerScore = peerPositive - peerNegative;
      const delta = candidateScore - peerScore;
      if (Math.abs(delta) < 0.03) return "";
      return axisSideLabel(axis, delta > 0);
    })
    .filter((label): label is string => Boolean(label))
    .slice(0, 2);
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
      <span className="font-body text-[13px] text-[#6B7280]">{label}</span>
      <span className="font-body text-[13px] font-semibold text-[#111827]">{value}</span>
    </div>
  );
}

function ProfileToggleButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-14 w-full items-center justify-between rounded-[16px] border border-[#ECE7DE] bg-white px-4 text-left font-body text-[14px] font-semibold text-[#111827] transition-all duration-200 hover:bg-[#FAFAF8]"
    >
      <span className="flex items-center gap-3">
        <FileText className="h-5 w-5 text-[#111827]" />
        <span>View full profile</span>
      </span>
      <ChevronDown className="h-5 w-5 text-[#6B7280]" />
    </button>
  );
}

function CandidateDetails({ candidate }: { candidate: Candidate }) {
  const sourcingConfidence = getSourcingConfidence(candidate);
  const recruiterMatchSummary = candidate.explanation?.aiReasoning || candidate.explanation?.summary?.[0] || "Strong recruiter match for this role.";

  return (
    <div className="space-y-5">
      <div className="rounded-[18px] border border-[#ECE7DE] bg-[#F8F7F3] p-5">
        <div className="space-y-3">
          <DetailRow label="Email" value={candidate.email || "Not provided"} />
          <DetailRow label="Role" value={candidate.headline || candidate.role || "Not provided"} />
          <DetailRow label="Location" value={candidate.location || "Not provided"} />
          <DetailRow label="Company" value={candidate.company || "Not provided"} />
          <DetailRow label="Experience" value={candidate.yearsExperience ? `${candidate.yearsExperience.toFixed(1)} years` : "Not provided"} />
          <DetailRow label="Recruiter match" value={recruiterMatchSummary} />
          <DetailRow label="Sourcing confidence" value={`${(sourcingConfidence * 100).toFixed(0)}%`} />
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Education</p>
          <ul className="mt-3 space-y-2 font-body text-[13px] text-[#4B5563]">
            {formatList(candidate.education).map((item) => (
              <li key={`education-${candidate.id}-${item}`} className="flex gap-2">
                <GraduationCap className="mt-0.5 h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Projects</p>
          <ul className="mt-3 space-y-2 font-body text-[13px] text-[#4B5563]">
            {formatList(candidate.projects).map((item) => (
              <li key={`project-${candidate.id}-${item}`} className="flex gap-2">
                <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Certifications</p>
          <ul className="mt-3 space-y-2 font-body text-[13px] text-[#4B5563]">
            {formatList(candidate.certifications).map((item) => (
              <li key={`cert-${candidate.id}-${item}`} className="flex gap-2">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Companies</p>
          <ul className="mt-3 space-y-2 font-body text-[13px] text-[#4B5563]">
            {formatList(candidate.companiesHistory).map((item) => (
              <li key={`company-${candidate.id}-${item}`} className="flex gap-2">
                <Building2 className="mt-0.5 h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5 md:col-span-2">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Domain experience</p>
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
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Summary</p>
          <p className="mt-3 font-body text-[13px] leading-6 text-[#4B5563]">{trimText(candidate.summary, 1200)}</p>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5 font-body text-[13px] text-[#4B5563]">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Scoring</p>
          <div className="mt-3 space-y-0.5">
            <DetailRow label="Sourcing confidence" value={`${(sourcingConfidence * 100).toFixed(0)}%`} />
            <DetailRow label="Fit score" value={`${candidate.fitScore.toFixed(1)} / 5`} />
            <DetailRow label="Status" value={statusLabel(candidate)} />
            <DetailRow label="Strategy" value={candidate.strategy} />
          </div>
        </div>

        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-5 font-body text-[13px] text-[#4B5563]">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Signals</p>
          <div className="mt-3 space-y-0.5">
            <DetailRow label="Resume included" value={candidate.resumeText ? "Yes" : "No"} />
            <DetailRow label="Mock email" value={candidate.isMockEmail ? "Yes" : "No"} />
            <DetailRow label="Matched skills" value={candidate.skills.length ? `${candidate.skills.slice(0, 4).join(", ")}` : "Not provided"} />
          </div>
        </div>
      </div>

      {candidate.resumeText && (
        <div className="rounded-[18px] border border-[#ECE7DE] bg-[#111111] p-5 font-body text-[13px] text-white">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-white/70">Resume text</p>
          <pre className="mt-3 whitespace-pre-wrap font-body leading-relaxed text-white/90">{candidate.resumeText}</pre>
        </div>
      )}

      {candidate.explanation?.sourceBreakdown && (
        <div className="grid gap-2 rounded-[18px] border border-[#ECE7DE] bg-white p-5 font-body text-[12px] text-[#6B7280] sm:grid-cols-2">
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
  badgeLabel,
  contrastLabels = [],
  isSelected,
  isSelecting,
  selectionLocked,
  onOpenDetails,
  onSelect,
  showSelectButton,
}: {
  candidate: Candidate;
  badgeLabel?: string;
  contrastLabels?: string[];
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
      className={`flex h-full min-h-[620px] flex-col rounded-[24px] border border-[#E7E0D4] bg-white p-0 shadow-[0_8px_24px_rgba(0,0,0,0.04)] transition-all duration-200 hover:-translate-y-1 hover:shadow-[0_14px_32px_rgba(0,0,0,0.08)] ${
        isSelected ? "ring-2 ring-[#DDF5E6]" : ""
      }`}
    >
      <CardHeader className="p-8 pb-0">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 space-y-3">
            <CardTitle className="font-heading text-[30px] font-bold leading-none text-[#111827]">
              {candidate.name || candidate.id.slice(0, 8)}
            </CardTitle>
            <div className="space-y-2 font-body text-[14px] leading-5 text-[#4B5563]">
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
          <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-[#DDF5E6] text-center font-body text-[14px] font-semibold leading-tight text-[#0F6B3A]">
            <span>{candidate.fitScore.toFixed(1)} / 5</span>
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex flex-1 flex-col p-8 pt-6">
        <div className="mb-4 inline-flex rounded-full bg-[#F3F4F6] px-3 py-1 font-body text-[11px] font-semibold text-[#6B7280]">
          {statusLabel(candidate)}
        </div>

        {badgeLabel && (
          <div className="mb-4 rounded-[14px] border border-dashed border-[#D7D0C2] bg-[#FBFAF7] px-4 py-3 text-xs font-medium text-[#6B7280]">
            {badgeLabel}
          </div>
        )}

        {contrastLabels.length > 0 && (
          <div className="mb-4 flex flex-wrap gap-2">
            {contrastLabels.map((label) => (
              <span key={`${candidate.id}-${label}`} className="rounded-full bg-[#EAF4FF] px-3 py-1 text-[11px] font-semibold text-[#1D4ED8]">
                {label}
              </span>
            ))}
          </div>
        )}

        <div className="rounded-[18px] border border-[#ECE7DE] bg-[#F8F7F3] p-5">
          <div className="flex items-center gap-3 border-b border-[#ECE7DE] py-2">
            <CircleUserRound className="h-4 w-4 shrink-0 text-[#6B7280]" />
            <span className="font-body text-[14px] text-[#4B5563]">Experience</span>
            <span className="ml-auto font-body text-[14px] font-semibold text-[#111827]">
              {candidate.yearsExperience ? `${candidate.yearsExperience.toFixed(1)} years` : "Not provided"}
            </span>
          </div>
          <div className="flex items-center gap-3 py-2">
            <Building2 className="h-4 w-4 shrink-0 text-[#6B7280]" />
            <span className="font-body text-[14px] text-[#4B5563]">Company</span>
            <span className="ml-auto font-body text-[14px] font-semibold text-[#111827]">{candidate.company || "Not provided"}</span>
          </div>
          <div className="flex items-center gap-3 border-t border-[#ECE7DE] py-2">
            <ShieldCheck className="h-4 w-4 shrink-0 text-[#6B7280]" />
            <span className="font-body text-[14px] text-[#4B5563]">Sourcing confidence</span>
            <span className="ml-auto font-body text-[14px] font-semibold text-[#111827]">
              {(getSourcingConfidence(candidate) * 100).toFixed(0)}%
            </span>
          </div>
        </div>

        <div className="mt-6">
          <ProfileToggleButton onClick={() => onOpenDetails()} />
        </div>

        <div className="mt-auto pt-6">
          <div className="border-t border-[#ECE7DE]" />
          <div className="pt-6">
            {showSelectButton && onSelect && (
              <Button
                data-testid={`batch-select-${candidate.id}`}
                className="h-14 w-full rounded-[14px] bg-[#0F6B3A] text-[15px] font-semibold text-white shadow-[0_8px_18px_rgba(15,107,58,0.18)] transition-colors duration-200 hover:bg-[#0C5A31]"
                onClick={(event) => {
                  event.stopPropagation();
                  onSelect();
                }}
                disabled={selectionLocked || isSelecting || isSelected}
              >
                {isSelected ? "Selected" : isSelecting ? "Selecting candidate..." : "Select candidate"}
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function CandidateListRow({
  candidate,
  rankLabel,
  isSelected,
  isSelecting,
  selectionLocked,
  onOpenDetails,
  onSelect,
}: {
  candidate: Candidate;
  rankLabel: string;
  isSelected: boolean;
  isSelecting: boolean;
  selectionLocked: boolean;
  onOpenDetails: () => void;
  onSelect: () => void;
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
      className={`group flex cursor-pointer flex-col rounded-[18px] border border-[#E8E0D4] bg-white p-5 text-left shadow-[0_6px_18px_rgba(0,0,0,0.03)] transition-all duration-200 hover:-translate-y-0.5 hover:border-[#DCCFBF] hover:shadow-[0_12px_24px_rgba(0,0,0,0.06)] ${
        isSelected ? "ring-2 ring-[#DDF5E6]" : ""
      }`}
    >
      <div className="flex items-start gap-4">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-[#E7F5EF] font-heading text-[14px] font-semibold text-[#0F6B3A]">
          {rankLabel}
        </div>

        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h3 className="font-heading text-[20px] font-semibold leading-tight text-[#111827]">
              {candidate.name || candidate.id.slice(0, 8)}
            </h3>
            <span className="text-[#A18E7C] transition-opacity group-hover:text-[#7D6A57]">↗</span>
          </div>
          <p className="font-body text-[14px] text-[#8A6F55]">{candidateSubtitle(candidate)}</p>
          <p style={clampLines(3)} className="max-w-4xl overflow-hidden font-body text-[15px] leading-6 text-[#5F564D]">
            {trimText(candidate.summary || candidate.headline || candidate.role || "No summary provided", 260)}
          </p>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1 pl-2 text-right">
          <div className="font-heading text-[28px] font-semibold leading-none text-[#0F6B3A]">{candidate.fitScore.toFixed(1)}</div>
          <div className="font-body text-[12px] text-[#8A6F55]">fit score</div>
        </div>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        {candidate.location && (
          <span className="rounded-full bg-[#F7F3EB] px-3 py-1 text-[12px] font-medium text-[#7D6A57]">
            {candidate.location}
          </span>
        )}
        {candidate.company && (
          <span className="rounded-full bg-[#F7F3EB] px-3 py-1 text-[12px] font-medium text-[#7D6A57]">
            {candidate.company}
          </span>
        )}
        {candidate.skills?.slice(0, 3).map((skill) => (
          <span key={`${candidate.id}-${skill}`} className="rounded-full bg-[#F4FBF7] px-3 py-1 text-[12px] font-medium text-[#0F6B3A]">
            {skill}
          </span>
        ))}
        <span className="rounded-full bg-[#EAF4FF] px-3 py-1 text-[12px] font-medium text-[#1D4ED8]">
          {(getSourcingConfidence(candidate) * 100).toFixed(0)}% match confidence
        </span>
      </div>

      <div className="mt-5 flex items-center justify-end gap-3">
        <Button
          data-testid={`review-details-${candidate.id}`}
          variant="outline"
          className="rounded-[12px] border-[#E3D9CA] bg-white px-4 py-2 text-[14px] font-semibold text-[#403325] hover:bg-[#FBF7F0]"
          onClick={(event) => {
            event.stopPropagation();
            onOpenDetails();
          }}
        >
          View details
        </Button>
        <Button
          data-testid={`review-select-${candidate.id}`}
          className="rounded-[12px] bg-[#0F6B3A] px-4 py-2 text-[14px] font-semibold text-white hover:bg-[#0C5A31]"
          onClick={(event) => {
            event.stopPropagation();
            onSelect();
          }}
          disabled={selectionLocked || isSelecting || isSelected}
        >
          {isSelected ? "Selected" : isSelecting ? "Selecting candidate..." : "Select candidate"}
        </Button>
      </div>
    </Card>
  );
}

function TimelineList({ items }: { items: any[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-gray-500">No ATS events recorded yet.</p>;
  }

  return (
    <div className="space-y-3">
      {items.slice(0, 8).map((item, index) => (
        <div key={`${String(item.type || "event")}-${String(item.createdAt || index)}`} className="rounded-xl border border-[#ECE7DE] bg-white p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Badge variant="neutral">{String(item.type || "event").replace(/_/g, " ")}</Badge>
            <span className="text-xs text-gray-500">{String(item.createdAt || "")}</span>
          </div>
          <p className="mt-2 text-sm font-medium text-gray-900">
            {item.toStatus ? `Moved to ${String(item.toStatus).replace(/_/g, " ")}` : item.status ? String(item.status).replace(/_/g, " ") : "Recorded event"}
          </p>
          <p className="text-xs text-gray-600">
            {item.source ? `Source: ${String(item.source).replace(/_/g, " ")}` : ""}
            {item.channel ? ` ${item.channel}` : ""}
          </p>
        </div>
      ))}
    </div>
  );
}

// ── Tinder-style swipe deck for final candidates ────────────────────────────

function SwipeDeck({
  candidates,
  shortlistedIds,
  isAdvancing,
  selectedCandidateId,
  onSelect,
  onReject,
  onOpenDetails,
}: {
  candidates: Candidate[];
  shortlistedIds: string[];
  isAdvancing: boolean;
  selectedCandidateId: string;
  onSelect: (id: string) => void;
  onReject: (id: string) => void;
  onOpenDetails: (c: Candidate) => void;
}) {
  // Track which candidates have been acted on in this session
  const [actedIds, setActedIds] = useState<Set<string>>(new Set());
  const [swipeDir, setSwipeDir] = useState<"left" | "right" | null>(null);
  const [swipingId, setSwipingId] = useState("");

  // Drag state
  const dragStartX = useRef(0);
  const dragCurrentX = useRef(0);
  const isDragging = useRef(false);
  const [dragOffset, setDragOffset] = useState(0);

  // Pending candidates = not yet acted on in this session
  const pending = candidates.filter((c) => !actedIds.has(c.id));
  const current = pending[0] ?? null;
  const next = pending[1] ?? null;

  const done = pending.length === 0;

  const triggerSwipe = (id: string, dir: "left" | "right") => {
    if (isAdvancing || swipingId) return;
    setSwipingId(id);
    setSwipeDir(dir);
    setTimeout(() => {
      setActedIds((prev) => new Set([...prev, id]));
      setSwipeDir(null);
      setSwipingId("");
      setDragOffset(0);
      if (dir === "right") onSelect(id);
      else onReject(id);
    }, 320);
  };

  // Pointer / touch drag handlers
  const onPointerDown = (e: React.PointerEvent) => {
    if (!current || isAdvancing || swipingId) return;
    isDragging.current = true;
    dragStartX.current = e.clientX;
    dragCurrentX.current = e.clientX;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!isDragging.current || !current) return;
    dragCurrentX.current = e.clientX;
    setDragOffset(dragCurrentX.current - dragStartX.current);
  };

  const onPointerUp = () => {
    if (!isDragging.current || !current) return;
    isDragging.current = false;
    const delta = dragCurrentX.current - dragStartX.current;
    if (delta > 80) triggerSwipe(current.id, "right");
    else if (delta < -80) triggerSwipe(current.id, "left");
    else setDragOffset(0);
  };

  if (done) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 rounded-[24px] border border-[#E7E0D4] bg-white py-16 text-center shadow-[0_8px_24px_rgba(0,0,0,0.04)]">
        <p className="font-heading text-[22px] font-semibold text-[#111827]">All candidates reviewed</p>
        <p className="font-body text-sm text-[#6B7280]">You've gone through all {candidates.length} candidates.</p>
      </div>
    );
  }

  // Rotation based on drag
  const rotation = Math.min(Math.max(dragOffset / 20, -12), 12);
  const overlayOpacity = Math.min(Math.abs(dragOffset) / 120, 1);
  const isRight = dragOffset > 0;

  // Animate-out transform when swipe is triggered by button
  const swipeTransform =
    swipingId === current?.id
      ? swipeDir === "right"
        ? "translateX(120%) rotate(20deg)"
        : "translateX(-120%) rotate(-20deg)"
      : dragOffset !== 0
        ? `translateX(${dragOffset}px) rotate(${rotation}deg)`
        : "translateX(0) rotate(0deg)";

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <p className="font-heading text-[22px] font-semibold text-[#111827]">Here are your top candidates</p>
          <p className="font-body text-sm text-[#8A6F55]">
            {pending.length} remaining · {shortlistedIds.length} selected
          </p>
        </div>
        <Badge variant="high">{candidates.length} candidates</Badge>
      </div>

      {/* Swipe hint */}
      <p className="text-center font-body text-xs text-[#9CA3AF]">
        Swipe right to select · Swipe left to reject · Tap card to view full profile
      </p>

      {/* Card stack */}
      <div className="relative mx-auto h-[520px] w-full max-w-[420px] select-none">
        {/* Next card (behind) */}
        {next && (
          <div
            className="absolute inset-0 scale-[0.96] rounded-[28px] border border-[#E7E0D4] bg-white shadow-[0_4px_16px_rgba(0,0,0,0.06)]"
            style={{ zIndex: 0 }}
          />
        )}

        {/* Current card */}
        {current && (
          <div
            className="absolute inset-0 cursor-grab rounded-[28px] border border-[#E7E0D4] bg-white shadow-[0_12px_32px_rgba(0,0,0,0.10)] active:cursor-grabbing"
            style={{
              zIndex: 1,
              transform: swipeTransform,
              transition: swipingId === current.id ? "transform 0.32s cubic-bezier(0.4,0,0.2,1)" : dragOffset !== 0 ? "none" : "transform 0.2s ease",
            }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onClick={() => {
              if (Math.abs(dragOffset) < 5) onOpenDetails(current);
            }}
          >
            {/* Swipe direction overlay */}
            {dragOffset !== 0 && (
              <>
                {/* Right = select (green) */}
                <div
                  className="pointer-events-none absolute inset-0 rounded-[28px] bg-[#DDF5E6]"
                  style={{ opacity: isRight ? overlayOpacity * 0.55 : 0 }}
                />
                {/* Left = reject (red) */}
                <div
                  className="pointer-events-none absolute inset-0 rounded-[28px] bg-[#FEE2E2]"
                  style={{ opacity: !isRight ? overlayOpacity * 0.55 : 0 }}
                />
                {/* Labels */}
                <div
                  className="pointer-events-none absolute left-5 top-6 rounded-xl border-4 border-[#0F6B3A] px-4 py-2 font-heading text-[22px] font-bold text-[#0F6B3A]"
                  style={{ opacity: isRight ? overlayOpacity : 0, transform: "rotate(-15deg)" }}
                >
                  SELECT
                </div>
                <div
                  className="pointer-events-none absolute right-5 top-6 rounded-xl border-4 border-[#DC2626] px-4 py-2 font-heading text-[22px] font-bold text-[#DC2626]"
                  style={{ opacity: !isRight ? overlayOpacity : 0, transform: "rotate(15deg)" }}
                >
                  PASS
                </div>
              </>
            )}

            {/* Card content */}
            <div className="flex h-full flex-col p-7">
              {/* Score badge */}
              <div className="mb-4 flex items-start justify-between">
                <div className="space-y-1">
                  <h3 className="font-heading text-[26px] font-bold leading-tight text-[#111827]">
                    {current.name || current.id.slice(0, 8)}
                  </h3>
                  <p className="font-body text-[14px] text-[#4B5563]">
                    {current.headline || current.role || ""}
                    {current.company ? ` @ ${current.company}` : ""}
                  </p>
                  {current.location && (
                    <p className="flex items-center gap-1 font-body text-[13px] text-[#9CA3AF]">
                      <MapPin className="h-3.5 w-3.5" />
                      {current.location}
                    </p>
                  )}
                </div>
                <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-[#DDF5E6] font-body text-[13px] font-semibold text-[#0F6B3A]">
                  {current.fitScore.toFixed(1)}/5
                </div>
              </div>

              {/* Summary */}
              {current.summary && (
                <p className="mb-4 line-clamp-3 font-body text-[14px] leading-6 text-[#5F564D]">
                  {current.summary}
                </p>
              )}

              {/* Skills */}
              {current.skills.length > 0 && (
                <div className="mb-4 flex flex-wrap gap-2">
                  {current.skills.slice(0, 5).map((skill) => (
                    <span
                      key={`${current.id}-${skill}`}
                      className="rounded-full bg-[#F4FBF7] px-3 py-1 text-[12px] font-medium text-[#0F6B3A]"
                    >
                      {skill}
                    </span>
                  ))}
                </div>
              )}

              {/* Experience */}
              {current.yearsExperience != null && (
                <p className="mb-4 font-body text-[13px] text-[#6B7280]">
                  <span className="font-semibold text-[#111827]">{current.yearsExperience.toFixed(1)} yrs</span> experience
                </p>
              )}

              {/* Already selected badge */}
              {(shortlistedIds.includes(current.id) || current.status === "shortlisted") && (
                <div className="mb-3 rounded-full bg-[#DDF5E6] px-4 py-1.5 text-center font-body text-[13px] font-semibold text-[#0F6B3A]">
                  ✓ Already selected
                </div>
              )}

              {/* Action buttons */}
              <div className="mt-auto flex gap-3">
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); triggerSwipe(current.id, "left"); }}
                  disabled={isAdvancing || Boolean(swipingId)}
                  className="flex h-14 flex-1 items-center justify-center gap-2 rounded-[16px] border-2 border-[#FCA5A5] bg-white font-body text-[15px] font-semibold text-[#DC2626] transition hover:bg-[#FEF2F2] disabled:opacity-50"
                >
                  <CircleX className="h-5 w-5" /> Pass
                </button>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); triggerSwipe(current.id, "right"); }}
                  disabled={isAdvancing || Boolean(swipingId)}
                  className="flex h-14 flex-1 items-center justify-center gap-2 rounded-[16px] bg-[#0F6B3A] font-body text-[15px] font-semibold text-white shadow-[0_6px_16px_rgba(15,107,58,0.22)] transition hover:bg-[#0C5A31] disabled:opacity-50"
                >
                  <CheckCircle2 className="h-5 w-5" /> Select
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Progress dots */}
      <div className="flex justify-center gap-1.5">
        {candidates.map((c) => (
          <div
            key={c.id}
            className={`h-2 w-2 rounded-full transition-colors ${
              shortlistedIds.includes(c.id) || c.status === "shortlisted"
                ? "bg-[#0F6B3A]"
                : actedIds.has(c.id)
                  ? "bg-[#D1D5DB]"
                  : c.id === current?.id
                    ? "bg-[#111827]"
                    : "bg-[#E5E7EB]"
            }`}
          />
        ))}
      </div>
    </div>
  );
}

export default function ReviewPage() {
  const router = useRouter();
  const { user, isSessionReady, jobId, isRefined } = useAppContext();

  const [session, setSession] = useState<CandidateSelectionSession | null>(null);
  const [intelligence, setIntelligence] = useState<RecruiterIntelligenceSession | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isCalibrationLoading, setIsCalibrationLoading] = useState(false);
  const [isAdvancing, setIsAdvancing] = useState(false);
  const [isContinuingToOutreach, setIsContinuingToOutreach] = useState(false);
  const [error, setError] = useState("");
  const [calibrationError, setCalibrationError] = useState("");
  const [calibrationSelectionId, setCalibrationSelectionId] = useState("");
  const [feedbackMessage, setFeedbackMessage] = useState("");
  const [selectionDebug, setSelectionDebug] = useState("");
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [activeCandidate, setActiveCandidate] = useState<Candidate | null>(null);
  const [activeInterviewInsights, setActiveInterviewInsights] = useState<any>(null);
  const [decisionNote, setDecisionNote] = useState("");
  const [decisionLoading, setDecisionLoading] = useState("");
  const [finalShortlistedIds, setFinalShortlistedIds] = useState<string[]>([]);
  const [candidateTimeline, setCandidateTimeline] = useState<any[]>([]);
  const [candidateNotifications, setCandidateNotifications] = useState<any[]>([]);
  const [isTimelineLoading, setIsTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState("");

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
      setCalibrationError("");
      setError("");

      const intelligenceResult = await getRecruiterIntelligence(user.id, jobId);
      if (cancelled) return;

      if (!intelligenceResult.success || !intelligenceResult.data) {
        setCalibrationError(intelligenceResult.error || "Could not load recruiter calibration.");
        setIntelligence(null);
        setIsLoading(false);
        return;
      }

      setIntelligence(intelligenceResult.data);

      const calibration = intelligenceResult.data.calibration;
      const calibrationReady = Boolean(calibration && calibration.stage === "real_sourcing_ready");
      const hasCurrentCalibration = Boolean(getCalibrationCurrentArchetypes(calibration).length > 0);

      if (!calibrationReady && hasCurrentCalibration) {
        setSession(null);
        setActiveCandidate(null);
        setFinalShortlistedIds([]);
        setSelectionDebug("");
        setIsLoading(false);
        return;
      }

      const selectionResult = await getFirstSelectionBatch(jobId);
      if (cancelled) return;

      if (!selectionResult.success || !selectionResult.data) {
        setError(selectionResult.error || "Could not load candidate selection.");
        setSelectionDebug("");
        setIsLoading(false);
        return;
      }

      setSession(selectionResult.data);
      setActiveCandidate(null);
      setFinalShortlistedIds([]);
      setIsLoading(false);
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [isSessionReady, jobId, user]);

  useEffect(() => {
    if (!isSessionReady || !user || !jobId || !activeCandidate) {
      setCandidateTimeline([]);
      setCandidateNotifications([]);
      setActiveInterviewInsights(null);
      setDecisionNote("");
      setDecisionLoading("");
      setTimelineError("");
      return;
    }

    let cancelled = false;
    const loadTimeline = async () => {
      setIsTimelineLoading(true);
      setTimelineError("");

      const [timelineResult, notificationsResult, insightsResult] = await Promise.all([
        getCandidateAtsTimeline(jobId, activeCandidate.id),
        getJobAtsNotifications(jobId),
        getInterviewInsights(jobId, activeCandidate.id),
      ]);

      if (cancelled) return;

      if (timelineResult.success && timelineResult.data) {
        setCandidateTimeline(timelineResult.data);
      } else {
        setCandidateTimeline([]);
        setTimelineError(timelineResult.error || "Could not load ATS timeline.");
      }

      if (notificationsResult.success && notificationsResult.data) {
        setCandidateNotifications(
          notificationsResult.data.filter((item) => !item.candidateId || item.candidateId === activeCandidate.id)
        );
      } else {
        setCandidateNotifications([]);
      }

      if (insightsResult.success && insightsResult.data) {
        setActiveInterviewInsights(insightsResult.data);
      } else {
        setActiveInterviewInsights(null);
      }

      setIsTimelineLoading(false);
    };

    void loadTimeline();
    return () => {
      cancelled = true;
    };
  }, [activeCandidate, isSessionReady, jobId, user]);

  const currentBatch = session?.currentBatch ?? [];
  const completed = Boolean(session?.completed);
  const progress = session ? Math.min(session.currentBatchIndex + (completed ? 0 : 1), session.totalBatches) : 0;
  const finalCandidates = session?.finalCandidates ?? session?.topCandidates ?? [];
  const analysis = session?.analysis ?? null;
  const pairAxes = useMemo(() => getPairAxes(session), [session]);
  const pairContrast = useMemo(() => pairAxes.map(humanizeContrastAxis), [pairAxes]);
  const pairRationale = useMemo(() => getPairRationale(session), [session]);
  const summaryLines = useMemo(() => analysisSummary(analysis), [analysis]);
  const calibration = intelligence?.calibration ?? null;
  const calibrationArchetypes = useMemo(() => getCalibrationCurrentArchetypes(calibration), [calibration]);
  const calibrationRoundLabel = useMemo(() => getCalibrationRoundLabel(calibration), [calibration]);
  const calibrationSetId = useMemo(() => getCalibrationCurrentSetId(calibration), [calibration]);
  const calibrationComplete = calibration?.stage === "real_sourcing_ready";
  const interviewProgression = activeInterviewInsights?.progression || [];
  const activeInterviewStage = interviewProgression.find((item: any) => item?.active) || interviewProgression[0] || null;
  const completedInterviewStages = interviewProgression.filter((item: any) => item?.completed).length;
  const completedShortlistedIds = useMemo(() => {
    const ids = new Set<string>(finalShortlistedIds);
    for (const candidate of finalCandidates) {
      if (candidate.status === "shortlisted") {
        ids.add(candidate.id);
      }
    }
    return [...ids];
  }, [finalCandidates, finalShortlistedIds]);
  const shortlistedCount = useMemo(() => {
    if (!completed) return session?.selectedCandidateIds.length ?? 0;
    return completedShortlistedIds.length;
  }, [completed, completedShortlistedIds, session?.selectedCandidateIds.length]);

  const markFinalSelectionLocally = (candidateId: string) => {
    setFinalShortlistedIds((prev) => (prev.includes(candidateId) ? prev : [...prev, candidateId]));
    setSession((prev) =>
      prev
        ? {
            ...prev,
            finalCandidates: (prev.finalCandidates || prev.topCandidates || []).map((candidate) =>
              candidate.id === candidateId ? { ...candidate, status: "shortlisted" } : candidate
            ),
            topCandidates: (prev.topCandidates || []).map((candidate) =>
              candidate.id === candidateId ? { ...candidate, status: "shortlisted" } : candidate
            ),
          }
        : prev
    );
  };

  const revertFinalSelectionLocally = (candidateId: string) => {
    setFinalShortlistedIds((prev) => prev.filter((id) => id !== candidateId));
    setSession((prev) =>
      prev
        ? {
            ...prev,
            finalCandidates: (prev.finalCandidates || prev.topCandidates || []).map((candidate) =>
              candidate.id === candidateId ? { ...candidate, status: "new" } : candidate
            ),
            topCandidates: (prev.topCandidates || []).map((candidate) =>
              candidate.id === candidateId ? { ...candidate, status: "new" } : candidate
            ),
          }
        : prev
    );
  };

  const syncFinalShortlist = async () => {
    if (!jobId || !completed || completedShortlistedIds.length === 0) {
      return true;
    }

    const results = await Promise.all(
      completedShortlistedIds.map((candidateId) => swipeCandidate({ jobId, candidateId, action: "accept" }))
    );
    const failed = results.find((result) => !result.success || !result.data);
    if (failed) {
      setError(failed.error || "Could not prepare shortlisted candidates for handoff.");
      setSelectionDebug(
        [
          `jobId=${jobId}`,
          `shortlistedIds=${completedShortlistedIds.join(", ")}`,
          `error=${failed.error || "Unknown error"}`
        ].join("\n")
      );
      return false;
    }

    return true;
  };

  const handleCalibrationSelect = async (archetypeId: string) => {
    if (!jobId || !user || isCalibrationLoading) return;
    setIsCalibrationLoading(true);
    setCalibrationError("");
    setCalibrationSelectionId(archetypeId);

    const result = await chooseRecruiterCalibrationArchetype(user.id, jobId, {
      jobId,
      candidateId: archetypeId,
      calibrationSetId,
    });

    const calibrationResult = result.data ?? null;
    if (!result.success || !calibrationResult) {
      setCalibrationError(result.error || "Could not save calibration choice.");
      setCalibrationSelectionId("");
      setIsCalibrationLoading(false);
      return;
    }

    setIntelligence((prev) =>
      prev
        ? {
            ...prev,
            calibration: calibrationResult.calibration ?? calibrationResult.selection ?? prev.calibration,
          }
        : prev
    );

    const nextCalibration = calibrationResult.calibration ?? calibrationResult.selection ?? null;
    const nextStage = String(nextCalibration?.stage || "").trim();
    if (nextStage === "real_sourcing_ready") {
      const selectionResult = await getFirstSelectionBatch(jobId);
      if (!selectionResult.success || !selectionResult.data) {
        setCalibrationError(selectionResult.error || "Calibration is complete, but candidate sourcing could not load.");
      } else {
        setSession(selectionResult.data);
        setActiveCandidate(null);
        setFinalShortlistedIds([]);
        setSelectionDebug("");
      }
    }

    setCalibrationSelectionId("");
    setIsCalibrationLoading(false);
  };

  const handleSelect = async (candidateId: string) => {
    if (!jobId || !session || isAdvancing) return;
    setIsAdvancing(true);
    setError("");
    setSelectedCandidateId(candidateId);

    if (completed) {
      markFinalSelectionLocally(candidateId);
      const result = await swipeCandidate({ jobId, candidateId, action: "accept" });
      if (!result.success || !result.data) {
        revertFinalSelectionLocally(candidateId);
        setError(result.error || "Could not shortlist candidate for handoff.");
        setSelectionDebug(`jobId=${jobId}\ncandidateId=${candidateId}\nerror=${result.error || "Unknown error"}`);
        setIsAdvancing(false);
        setSelectedCandidateId("");
        return;
      }

      const outreachResult = await sendOutreach({ jobId, selectedCandidates: [candidateId] });
      if (!outreachResult.success || !outreachResult.data) {
        setError(outreachResult.error || "Selection saved, but automation handoff could not be completed.");
      } else {
        setFeedbackMessage(
          outreachResult.data.sent > 0
            ? `Adam is handling the handoff and ATS updates for ${outreachResult.data.sent} candidate${outreachResult.data.sent === 1 ? "" : "s"}.`
            : "Selection saved. Adam is handling the handoff."
        );
      }
      setActiveCandidate(null);
      setIsAdvancing(false);
      setSelectedCandidateId("");
      return;
    }

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

  const handleReject = async (candidateId: string) => {
    if (!jobId || isAdvancing) return;
    setIsAdvancing(true);
    setSelectedCandidateId(candidateId);
    await swipeCandidate({ jobId, candidateId, action: "reject" });
    setIsAdvancing(false);
    setSelectedCandidateId("");
  };

  const handleContinueToOutreach = async () => {
    if (!jobId || !session || !completed || shortlistedCount === 0 || isContinuingToOutreach) return;
    setIsContinuingToOutreach(true);
    setError("");

    const ok = await syncFinalShortlist();
    if (ok) {
      const shortlistedCandidates = (session.finalCandidates || session.topCandidates || []).filter(
        (candidate) => completedShortlistedIds.includes(candidate.id)
      );
      storeShortlistedCandidateIds(jobId, completedShortlistedIds);
      storeShortlistedCandidates(jobId, shortlistedCandidates);
      router.push("/ready");
    }

    setIsContinuingToOutreach(false);
  };

  const handleInterviewDecision = async (action: string, targetStage?: string) => {
    if (!jobId || !activeCandidate || decisionLoading) return;
    setDecisionLoading(action);
    setError("");
    const result = await submitInterviewDecision({
      jobId,
      candidateId: activeCandidate.id,
      action,
      targetStage,
      notes: decisionNote.trim(),
      sourceType: "adam",
    });

    if (!result.success || !result.data) {
      setError(result.error || "Could not update interview decision.");
      setDecisionLoading("");
      return;
    }

    setCandidateTimeline([]);
    setCandidateNotifications([]);
    setActiveInterviewInsights(result.data as any);
    setDecisionNote("");
    const refreshedTimeline = await getCandidateAtsTimeline(jobId, activeCandidate.id);
    if (refreshedTimeline.success && refreshedTimeline.data) {
      setCandidateTimeline(refreshedTimeline.data);
    }
    const refreshedNotifications = await getJobAtsNotifications(jobId);
    if (refreshedNotifications.success && refreshedNotifications.data) {
      setCandidateNotifications(
        refreshedNotifications.data.filter((item) => !item.candidateId || item.candidateId === activeCandidate.id)
      );
    }
    setDecisionLoading("");
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
      <div className="mx-auto w-full max-w-[1600px] space-y-6 px-4 py-6 font-body sm:px-6 lg:px-8 2xl:px-6">
        <div className="w-full rounded-[32px] border border-[#E7E0D4] bg-[#F8F5EE] p-6 shadow-sm md:p-8 lg:p-10">
          <div className="mb-6 flex items-center justify-between gap-4">
            <div className="space-y-1">
              <h1 className="font-body text-2xl font-semibold tracking-tight text-[#111827]">Review Candidates</h1>
              <p className="font-body text-sm text-[#6B7280]">A refined shortlist review built for fast, confident selection.</p>
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
              <span className="font-body text-[15px] font-semibold text-[#111827]">
                Progress: <span className="text-[#0F6B3A]">{session ? `${progress} / ${session.totalBatches}` : "0 / 3"}</span>
              </span>
            </div>
            <div className="flex items-center justify-center gap-3 border-r border-[#ECE7DE] px-4">
              <Users className="h-5 w-5 text-[#0F6B3A]" />
              <span className="font-body text-[15px] font-semibold text-[#111827]">
                Selected: <span className="text-[#0F6B3A]">{shortlistedCount}</span>
              </span>
            </div>
            <div className="flex items-center justify-center gap-3 px-4">
              <CircleX className="h-5 w-5 text-[#0F6B3A]" />
              <span className="font-body text-[15px] font-semibold text-[#111827]">
                Rejected: <span className="text-[#0F6B3A]">{session?.rejectedCandidateIds.length ?? 0}</span>
              </span>
            </div>
          </div>

          {isLoading && <p className="text-sm text-gray-500">Loading selection session...</p>}
          {error && <p className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}

          {!isLoading && calibration && !calibrationComplete && calibrationArchetypes.length > 0 && (
            <div className="space-y-8 pt-4 md:pt-6">
              <div className="flex flex-col items-start justify-between gap-5 md:flex-row md:items-center">
                <div className="space-y-2 md:pr-4">
                  <p className="font-body text-[11px] font-semibold uppercase tracking-[0.24em] text-[#0F6B3A]">
                    Preference calibration {calibrationRoundLabel}
                  </p>
                  <p className="max-w-3xl font-body text-sm leading-6 text-[#6B7280]">
                    Adam generated two hiring archetypes for this set. Choose the one that best matches your preferred hiring style.
                  </p>
                </div>
                <Badge className="inline-flex whitespace-nowrap rounded-full bg-[#EAF4FF] px-5 py-2 text-[13px] font-semibold text-[#1D4ED8] shadow-none">
                  Calibration gate
                </Badge>
              </div>

              <div className="grid gap-6 md:grid-cols-2">
                {calibrationArchetypes.map((archetype) => {
                  const archetypeId = String(archetype.id || archetype.archetype_id || "").trim();
                  const profileData = (archetype.profileData && typeof archetype.profileData === "object" ? archetype.profileData : {}) as Record<string, unknown>;
                  const title = calibrationText(
                    profileData.candidateHeadline ||
                      profileData.candidate_headline ||
                      archetype.title ||
                      archetype.name ||
                      archetype.role ||
                      "Archetype"
                  );
                  const experienceSnapshot = calibrationText(profileData.experienceSnapshot || profileData.experience_snapshot || archetype.headline);
                  const careerPattern = calibrationText(profileData.careerPattern || profileData.career_pattern);
                  const strengths = calibrationValueList(
                    profileData.technicalStrengths ||
                      profileData.technical_strengths ||
                      profileData.strengths ||
                      archetype.strengths ||
                      archetype.skills
                  );
                  const isChoosing = calibrationSelectionId === archetypeId;
                  const ownership = calibrationText(profileData.ownershipStyle || profileData.ownership_style || profileData.ownershipLevel || profileData.ownership_level);
                  const environment = calibrationText(profileData.idealEnvironment || profileData.ideal_environment || archetype.ideal_environment || archetype.idealEnvironment);
                  const execution = calibrationText(profileData.executionStyle || profileData.execution_style || archetype.execution_style || archetype.executionStyle);
                  const risk = calibrationText(profileData.riskTolerance || profileData.risk_tolerance || archetype.risk_tolerance || archetype.riskTolerance);
                  const leadership = calibrationText(profileData.leadershipProfile || profileData.leadership_profile || profileData.leadershipSignals || profileData.leadership_signals || archetype.leadership_signals || archetype.leadershipSignals);
                  const tradeoffs = calibrationText(profileData.hiringTradeoffs || profileData.hiring_tradeoffs || archetype.hiring_tradeoffs || archetype.hiringTradeoffs);
                  const fitNote = calibrationText(profileData.fitNote || profileData.fit_note || archetype.fit_note || archetype.fitNote);

                  return (
                    <Card key={archetypeId || title} className="rounded-[24px] border border-[#E7E0D4] bg-white shadow-[0_8px_24px_rgba(0,0,0,0.04)]">
                      <CardHeader className="space-y-3">
                        <div className="flex items-start justify-between gap-4">
                          <div className="space-y-2">
                            <CardTitle className="font-heading text-[24px] font-semibold text-[#111827]">{title}</CardTitle>
                            <CardDescription className="font-body text-sm leading-6 text-[#6B7280]">
                              {experienceSnapshot || fitNote || "Believable ideal candidate profile for calibration."}
                            </CardDescription>
                          </div>
                          <Badge className="rounded-full bg-[#F5E7B8] px-3 py-1 text-[12px] font-semibold text-[#8A5A00] shadow-none">
                            Archetype
                          </Badge>
                        </div>
                        {careerPattern && (
                          <p className="font-body text-[12px] leading-6 text-[#0F6B3A]">
                            <span className="font-semibold text-[#111827]">Career pattern:</span> {careerPattern}
                          </p>
                        )}
                      </CardHeader>
                      <CardContent className="space-y-4">
                        {strengths.length > 0 && (
                          <div>
                            <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Technical strengths</p>
                            <div className="flex flex-wrap gap-2">
                              {strengths.slice(0, 4).map((strength) => (
                                <span key={`${archetypeId}-${strength}`} className="rounded-full bg-[#F4FBF7] px-3 py-1 text-[12px] font-medium text-[#0F6B3A]">
                                  {strength}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                        <div className="grid gap-3 rounded-[18px] border border-[#ECE7DE] bg-[#FBFAF7] p-4 text-sm text-[#4B5563]">
                          {ownership && <p><span className="font-semibold text-[#111827]">Ownership style:</span> {ownership}</p>}
                          {environment && <p><span className="font-semibold text-[#111827]">Ideal environment:</span> {environment}</p>}
                          {execution && <p><span className="font-semibold text-[#111827]">Execution style:</span> {execution}</p>}
                          {risk && <p><span className="font-semibold text-[#111827]">Risk tolerance:</span> {risk}</p>}
                          {leadership && <p><span className="font-semibold text-[#111827]">Leadership profile:</span> {leadership}</p>}
                          {tradeoffs && <p><span className="font-semibold text-[#111827]">Hiring tradeoffs:</span> {tradeoffs}</p>}
                        </div>
                        <Button
                          data-testid={`calibration-select-${archetypeId}`}
                          className="h-12 w-full rounded-[14px] bg-[#0F6B3A] text-[15px] font-semibold text-white shadow-[0_8px_18px_rgba(15,107,58,0.18)] transition-colors duration-200 hover:bg-[#0C5A31]"
                          onClick={() => void handleCalibrationSelect(archetypeId)}
                          disabled={isCalibrationLoading || !archetypeId}
                        >
                          {isChoosing ? "Saving selection..." : "Select archetype"}
                        </Button>
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            </div>
          )}

          {!isLoading && calibration && !calibrationComplete && calibrationArchetypes.length === 0 && (
            <div className="rounded-[20px] border border-[#DDF5E6] bg-[#F4FBF7] px-4 py-3 text-sm text-[#0F6B3A]">
              Calibration is being prepared. Adam will show the archetype sets here shortly.
            </div>
          )}

          {calibrationError && <p className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{calibrationError}</p>}

          {!isLoading && calibrationComplete && !session && (
            <div className="rounded-[20px] border border-[#E7E0D4] bg-white px-4 py-3 text-sm text-[#6B7280]">
              Calibration is complete. Adam is now building your personalized shortlist.
            </div>
          )}

          {!isLoading && !completed && currentBatch.length > 0 && (
            <div className="space-y-8 pt-4 md:pt-6">
              <div className="flex flex-col items-start justify-between gap-5 md:flex-row md:items-center">
                <div className="space-y-2 md:pr-4">
                  <p className="font-body text-[11px] font-semibold uppercase tracking-[0.24em] text-[#0F6B3A]">
                    Batch {session?.currentBatchIndex ? session.currentBatchIndex + 1 : 1} of {session?.totalBatches ?? 3}
                  </p>
                  <p className="max-w-3xl font-body text-sm leading-6 text-[#6B7280]">
                    Review the candidates in this set. Expand their profile to see full details and choose the one you want to keep.
                  </p>
                </div>
                <Badge className="inline-flex whitespace-nowrap rounded-full bg-[#F5E7B8] px-5 py-2 text-[13px] font-semibold text-[#8A5A00] shadow-none">
                  2-candidate set
                </Badge>
              </div>

              {(pairRationale || pairContrast.length > 0) && (
                <div className="grid gap-4 rounded-[24px] border border-[#E7E0D4] bg-white p-5 shadow-[0_8px_24px_rgba(0,0,0,0.04)] lg:grid-cols-[1.2fr_0.8fr]">
                  <div className="space-y-2">
                    <p className="font-body text-[11px] font-semibold uppercase tracking-[0.22em] text-[#0F6B3A]">Why this pair</p>
                    <p className="font-body text-sm leading-6 text-[#4B5563]">{pairRationale}</p>
                  </div>
                  <div className="space-y-3">
                    <p className="font-body text-[11px] font-semibold uppercase tracking-[0.22em] text-[#0F6B3A]">Contrast axes</p>
                    <div className="flex flex-wrap gap-2">
                      {pairContrast.length > 0 ? (
                        pairContrast.map((axis) => (
                          <Badge key={axis} className="rounded-full border border-[#E7E0D4] bg-[#FBFAF7] px-3 py-1 text-[12px] font-semibold text-[#111827]">
                            {axis}
                          </Badge>
                        ))
                      ) : (
                        <span className="font-body text-sm text-[#6B7280]">This round is contrasting two different profile shapes.</span>
                      )}
                    </div>
                  </div>
                </div>
              )}

              <div className="grid gap-8 md:grid-cols-2">
                {currentBatch.map((candidate, index) => {
                  const peer = currentBatch[index === 0 ? 1 : 0];
                  const peerLabel =
                    peer && currentBatch.length === 2
                      ? `Compared with ${peer.name || peer.headline || peer.role || "the other profile"}`
                      : undefined;
                  const contrastLabels = getPairContrastLabels(candidate, peer, pairAxes);
                  return (
                    <CandidateCard
                      key={candidate.id}
                      candidate={candidate}
                      badgeLabel={peerLabel}
                      contrastLabels={contrastLabels}
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
                <p className="mt-1">The backend has analyzed your choices and reranked the full candidate pool using the signals you showed. Adam will handle enrichment, handoff, and ATS updates in the background.</p>
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

              <SwipeDeck
                candidates={finalCandidates}
                shortlistedIds={completedShortlistedIds}
                isAdvancing={isAdvancing}
                selectedCandidateId={selectedCandidateId}
                onSelect={(id) => void handleSelect(id)}
                onReject={(id) => void handleReject(id)}
                onOpenDetails={(c) => setActiveCandidate(c)}
              />

              <Separator />

              <div className="grid gap-3 md:grid-cols-2">
                <Button
                  data-testid="continue-to-ready"
                  className="w-full justify-center rounded-[14px] bg-[#0F6B3A] text-[15px] font-semibold text-white hover:bg-[#0C5A31]"
                  onClick={() => void handleContinueToOutreach()}
                  disabled={shortlistedCount === 0 || isAdvancing || isContinuingToOutreach}
                >
                  {isContinuingToOutreach
                    ? "Preparing handoff..."
                    : shortlistedCount > 0
                      ? "Continue to Ready"
                      : "Select candidates to continue"}
                </Button>
                <Button variant="outline" className="w-full justify-center rounded-[14px] border-[#E7E0D4] bg-white text-[15px] font-semibold text-[#111827]" onClick={() => void refreshFinalResults()}>
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

                <div className="rounded-[18px] border border-[#ECE7DE] bg-[#FBF8F1] p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">ATS status</p>
                      <h3 className="mt-1 font-heading text-[18px] font-semibold text-[#111827]">{atsStatusLabel(activeCandidate)}</h3>
                      <p className="mt-1 text-sm text-[#6B7280]">
                        {activeCandidate.ats_status_reason || "Canonical ATS state tracked through orchestration and review."}
                      </p>
                    </div>
                    <div className="text-right text-xs text-[#6B7280]">
                      <p>Source: {activeCandidate.ats_status_source || "system"}</p>
                      <p>Updated: {activeCandidate.ats_status_updated_at || "n/a"}</p>
                      <p>
                        Enrichment: {activeCandidate.enrichmentStatus || "pending"}
                        {activeCandidate.enrichmentSource ? ` via ${activeCandidate.enrichmentSource}` : ""}
                      </p>
                    </div>
                  </div>

                  <div className="mt-4 grid gap-4 lg:grid-cols-2">
                    <div className="rounded-2xl border border-[#ECE7DE] bg-white p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Interview stage</p>
                          <h4 className="mt-1 font-semibold text-[#111827]">
                            {String(activeInterviewStage?.label || activeInterviewInsights?.currentStage || "Recruiter screen")}
                          </h4>
                        </div>
                        <Badge variant="neutral">{completedInterviewStages} completed</Badge>
                      </div>
                      <div className="mt-3 space-y-2 text-sm text-[#4B5563]">
                        <p>Recommendation: {String(activeInterviewInsights?.intelligence?.recommendationSignal || "n/a")}</p>
                        <p>Confidence: {String(activeInterviewInsights?.intelligence?.interviewQualityScore ?? 0)}</p>
                        <p>Workflow token: {String(activeInterviewInsights?.workflowToken || activeCandidate.id).slice(0, 12)}…</p>
                        <p>Evaluations: {String(activeInterviewInsights?.evaluationCount ?? 0)}</p>
                        <p>
                          Contact: {activeCandidate.contactEmail || "pending"}
                          {activeCandidate.contactPhone ? ` · ${activeCandidate.contactPhone}` : ""}
                        </p>
                      </div>
                      <div className="mt-4 flex flex-wrap gap-2">
                        {(activeInterviewInsights?.progression || []).slice(0, 6).map((item: any) => (
                          <Badge
                            key={String(item.stage || item.label || "")}
                            variant={item.active ? "high" : item.completed ? "neutral" : "low"}
                          >
                            {String(item.label || item.stage || "").replace(/_/g, " ")}
                          </Badge>
                        ))}
                      </div>
                      <div className="mt-3 rounded-xl bg-[#F8F7F3] p-3 text-xs text-[#6B7280]">
                        <p className="font-medium text-[#111827]">Next-stage recommendation</p>
                        <p>{String(activeInterviewStage?.stage || activeInterviewInsights?.currentStage || "recruiter_screen").replace(/_/g, " ")}</p>
                      </div>
                    </div>

                    <div className="rounded-2xl border border-[#ECE7DE] bg-white p-4">
                      <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Recruiter decision</p>
                      <textarea
                        value={decisionNote}
                        onChange={(event) => setDecisionNote(event.target.value)}
                        placeholder="Add decision notes for the next stage, offer, or rejection."
                        className="mt-3 min-h-[92px] w-full rounded-xl border border-[#E7E0D4] bg-white px-3 py-2 text-sm text-[#111827] outline-none transition focus:border-[#0F6B3A]"
                      />
                      <div className="mt-3 grid gap-2 sm:grid-cols-2">
                        <Button
                          variant="outline"
                          className="justify-center"
                          disabled={Boolean(decisionLoading)}
                          onClick={() => void handleInterviewDecision("advance")}
                        >
                          {decisionLoading === "advance" ? "Advancing..." : "Advance next round"}
                        </Button>
                        <Button
                          variant="outline"
                          className="justify-center"
                          disabled={Boolean(decisionLoading)}
                          onClick={() => void handleInterviewDecision("mark_offer")}
                        >
                          {decisionLoading === "mark_offer" ? "Updating..." : "Mark offer"}
                        </Button>
                        <Button
                          variant="outline"
                          className="justify-center"
                          disabled={Boolean(decisionLoading)}
                          onClick={() => void handleInterviewDecision("mark_placed")}
                        >
                          {decisionLoading === "mark_placed" ? "Updating..." : "Mark placed"}
                        </Button>
                        <Button
                          variant="outline"
                          className="justify-center"
                          disabled={Boolean(decisionLoading)}
                          onClick={() => void handleInterviewDecision("archive")}
                        >
                          {decisionLoading === "archive" ? "Updating..." : "Archive"}
                        </Button>
                        <Button
                          variant="outline"
                          className="justify-center text-red-700 hover:bg-red-50"
                          disabled={Boolean(decisionLoading)}
                          onClick={() => void handleInterviewDecision("reject")}
                        >
                          {decisionLoading === "reject" ? "Updating..." : "Reject"}
                        </Button>
                        <Button
                          variant="outline"
                          className="justify-center"
                          disabled={Boolean(decisionLoading)}
                          onClick={() => void handleInterviewDecision("no_show")}
                        >
                          {decisionLoading === "no_show" ? "Updating..." : "Mark no-show"}
                        </Button>
                      </div>
                    </div>
                  </div>

                  <div className="mt-4 grid gap-4 lg:grid-cols-2">
                    <div className="rounded-2xl border border-[#ECE7DE] bg-white p-4">
                      <div className="flex items-center justify-between gap-3">
                        <h4 className="font-body text-sm font-semibold text-[#111827]">Candidate timeline</h4>
                        {isTimelineLoading ? <span className="text-xs text-gray-500">Loading...</span> : null}
                      </div>
                      <div className="mt-3">
                        {timelineError ? <p className="text-sm text-red-600">{timelineError}</p> : <TimelineList items={candidateTimeline} />}
                      </div>
                    </div>

                    <div className="rounded-2xl border border-[#ECE7DE] bg-white p-4">
                      <div className="flex items-center justify-between gap-3">
                        <h4 className="font-body text-sm font-semibold text-[#111827]">Notifications</h4>
                        <span className="text-xs text-gray-500">{candidateNotifications.length} items</span>
                      </div>
                      <div className="mt-3 space-y-3">
                        {candidateNotifications.length === 0 ? (
                          <p className="text-sm text-gray-500">No recruiter or candidate notifications yet.</p>
                        ) : (
                          candidateNotifications.slice(0, 5).map((item) => (
                            <div key={String(item.id)} className="rounded-xl border border-[#ECE7DE] bg-[#FBF8F1] p-3">
                              <div className="flex items-center justify-between gap-2">
                                <Badge variant="neutral">{String(item.channel || "notification")}</Badge>
                                <span className="text-xs text-gray-500">{String(item.createdAt || "")}</span>
                              </div>
                              <p className="mt-2 text-sm font-medium text-gray-900">{String(item.title || "")}</p>
                              <p className="text-xs text-gray-600">{String(item.status || "")}</p>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="flex flex-col gap-3 md:flex-row">
                  <Button
                    data-testid={`final-select-${activeCandidate.id}`}
                    className="w-full justify-center rounded-[14px] bg-[#0F6B3A] text-[16px] font-semibold text-white hover:bg-[#0C5A31] md:w-auto md:flex-1"
                    onClick={() => void handleSelect(activeCandidate.id)}
                    disabled={isAdvancing || selectedCandidateId !== "" || finalShortlistedIds.includes(activeCandidate.id) || activeCandidate.status === "shortlisted"}
                  >
                    {isAdvancing && selectedCandidateId === activeCandidate.id
                      ? "Selecting candidate..."
                      : finalShortlistedIds.includes(activeCandidate.id) || activeCandidate.status === "shortlisted"
                        ? "Selected"
                        : "Select this candidate"}
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
          <div className="mt-6 flex items-center justify-center gap-2 font-body text-sm text-[#6B7280]">
            <ShieldCheck className="h-4 w-4 text-[#0F6B3A]" />
            <span>Your selection helps us improve future matches</span>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
