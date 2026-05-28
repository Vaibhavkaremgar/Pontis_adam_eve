"use client";

/**
 * What this file does:
 * Runs ideal candidate profile selection and the post-selection X-Ray review flow.
 *
 * What API it connects to:
 * GET /recruiters/:recruiterId/intelligence/jobs/:jobId
 * POST /recruiters/:recruiterId/intelligence/jobs/:jobId/choice
 * GET /candidates?jobId=...&refresh=true
 *
 * How it fits in the pipeline:
 * Voice intake -> ideal candidate profile generation -> X-Ray sourcing -> recruiter review
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowUpRight,
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
import { useAppContext } from "@/context/AppContext";
import { getCandidateAtsTimeline, getJobAtsNotifications } from "@/lib/api/ats";
import { getCandidates, selectCandidateForEnrichment, swipeCandidate } from "@/lib/api/candidates";
import { chooseRecruiterCalibrationArchetype, getRecruiterIntelligence } from "@/lib/api/recruiter-intelligence";
import { getInterviewInsights, submitInterviewDecision } from "@/lib/api/interviews";
import {
  getStoredReviewCandidates,
  getStoredShortlistedCandidateIds,
  storeReviewCandidates,
  storeShortlistedCandidateIds,
  storeShortlistedCandidates,
} from "@/lib/session";
import type { Candidate, CandidateSelectionAnalysis, CandidateSelectionSession } from "@/types";
import type { RecruiterIntelligenceSession } from "@/lib/api/recruiter-intelligence";

function statusLabel(candidate: Candidate): string {
  const normalizedStatus = String(candidate.status || "").trim().toLowerCase();
  if (normalizedStatus === "sourced") return "LinkedIn sourced";
  if (normalizedStatus === "reviewed") return "Reviewed";
  if (normalizedStatus === "selected" || normalizedStatus === "shortlisted") return "Shortlisted";
  if (normalizedStatus === "enriching") return "Enriching";
  if (normalizedStatus === "enriched") return "Enriched";
  if (normalizedStatus === "outreach_pending") return "Outreach pending";
  if (normalizedStatus === "outreach_sent") return "Outreach sent";
  if (normalizedStatus === "rejected") return "Rejected";
  return "Awaiting choice";
}

function isShortlistedStatus(value: unknown): boolean {
  const normalized = String(value || "").trim().toLowerCase();
  return ["selected", "shortlisted", "accepted"].includes(normalized);
}

function atsStatusLabel(candidate: Candidate): string {
  return (candidate.ats_status || candidate.status || "reviewed").replace(/_/g, " ");
}

function candidateSubtitle(candidate: Candidate): string {
  return [getCandidateCurrentRole(candidate) || "", candidate.company ? candidate.company : "", getCandidateLocation(candidate) || ""]
    .filter(Boolean)
    .join(" - ");
}

function getCandidateLinkedInUrl(candidate: Candidate): string {
  if (candidate.linkedinUrl) return candidate.linkedinUrl.trim();
  const profileData = candidate.profileData && typeof candidate.profileData === "object" ? candidate.profileData : {};
  return String(profileData.linkedin_url || profileData.linkedinUrl || profileData.linkedInUrl || "").trim();
}

function getCandidateCurrentRole(candidate: Candidate): string {
  const profileData = candidate.profileData && typeof candidate.profileData === "object" ? candidate.profileData : {};
  const values = [
    profileData.current_role,
    profileData.currentRole,
    profileData.role,
    profileData.title,
    candidate.role,
    candidate.headline,
  ]
    .map((value) => String(value || "").trim())
    .filter((value) => value && !isLikelySummaryText(value) && !looksLikeSkillList(value, getCandidateSkills(candidate)));
  return values[0] || "";
}

function getCandidateProfileData(candidate: Candidate): Record<string, unknown> {
  return candidate.profileData && typeof candidate.profileData === "object" ? candidate.profileData : {};
}

function getCandidateRawDiscovery(candidate: Candidate): Record<string, unknown> {
  if (candidate.rawDiscovery && typeof candidate.rawDiscovery === "object") return candidate.rawDiscovery;
  const profileData = getCandidateProfileData(candidate);
  const rawDiscovery = profileData.raw_discovery || profileData.rawDiscovery;
  return rawDiscovery && typeof rawDiscovery === "object" ? rawDiscovery as Record<string, unknown> : {};
}

function splitCandidateTextList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item || "").trim()).filter(Boolean);
  }
  if (typeof value !== "string") return [];
  return value
    .replace(/[â€¢Â·]/g, ",")
    .split(/[,/|;]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function getCandidateSkills(candidate: Candidate): string[] {
  const profileData = getCandidateProfileData(candidate);
  const merged = [...splitCandidateTextList(candidate.skills), ...splitCandidateTextList(profileData.skills)];
  const deduped: string[] = [];
  const seen = new Set<string>();
  for (const skill of merged) {
    const key = skill.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(skill);
  }
  return deduped;
}

function isLikelySummaryText(value: string): boolean {
  const normalized = String(value || "").trim();
  if (!normalized) return false;
  const words = normalized.split(/\s+/).filter(Boolean);
  if (normalized.length > 120 || words.length > 16) return true;
  return /[.!?]/.test(normalized) && words.length > 10;
}

function looksLikeSkillList(value: string, skills: string[]): boolean {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized) return false;
  const skillTokens = skills.map((skill) => skill.trim().toLowerCase()).filter(Boolean);
  if (skillTokens.length > 0 && skillTokens.every((skill) => normalized.includes(skill))) return true;
  const parts = normalized.split(/[,/|Â·â€¢]/).map((part) => part.trim()).filter(Boolean);
  if (parts.length >= 3 && parts.every((part) => part.length <= 24)) return true;
  const techMarkers = ["javascript", "typescript", "python", "react", "node", "html", "css", "sql", "aws", "docker", "kubernetes"];
  return techMarkers.some((marker) => normalized.includes(marker)) && parts.length >= 2;
}

function getCandidateLocation(candidate: Candidate): string {
  const profileData = getCandidateProfileData(candidate);
  const skills = getCandidateSkills(candidate);
  const candidates = [
    profileData.location,
    profileData.current_location,
    profileData.currentLocation,
    candidate.location,
  ]
    .map((item) => String(item || "").trim())
    .filter(Boolean);

  for (const location of candidates) {
    if (!looksLikeSkillList(location, skills)) return location;
  }
  return "";
}

function isInteractiveElement(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(target.closest("button, a, [role='button'], input, textarea, select, label"));
}

function getSnippetQualityLabel(candidate: Candidate): string {
  const profileData = getCandidateProfileData(candidate);
  const quality = String(candidate.snippetQuality || profileData.snippet_quality || "").toLowerCase();
  if (quality === "rich") return "Rich snippet";
  if (quality === "thin") return "Thin snippet";
  if (quality === "partial") return "Partial snippet";
  return "Snippet quality unknown";
}

function getReasoningSummary(candidate: Candidate): string {
  const explanation = candidate.explanation;
  if (explanation?.aiReasoning) return explanation.aiReasoning;
  const profileData = getCandidateProfileData(candidate);
  const quality = String(candidate.snippetQuality || profileData.snippet_quality || "").toLowerCase();
  if (quality === "rich") return "High-signal profile with enough detail for confident review.";
  if (quality === "partial") return "Moderate signal profile with enough context to keep in the review set.";
  return "Low-information profile kept in the queue so recruiters do not lose potentially relevant candidates.";
}

function renderSignals(candidate: Candidate) {
  const explanation = candidate.explanation;
  const penalties = explanation?.penalties ?? {};
  const semantic = explanation?.semanticScore ?? explanation?.semantic ?? 0;
  const matchedSkills = explanation?.skillsMatched ?? explanation?.skills_match ?? [];
  const experienceMatch = explanation?.experienceMatch || explanation?.candidateExperience || explanation?.jobExperience || "";
  const semanticLabel = semantic >= 0.7 ? "Strong semantic match" : semantic >= 0.45 ? "Solid semantic match" : "Light semantic signal";

  return (
    <div className="space-y-1 rounded-xl bg-white/70 p-3 text-xs text-gray-600">
      <p>
        Semantic match: <span className="font-medium text-gray-800">{semanticLabel}</span>
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
          Recruiter preference signal applied
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

function getCalibrationCurrentProfiles(calibration: RecruiterIntelligenceSession["calibration"] | null | undefined): Array<Record<string, unknown>> {
  const currentPair = (calibration?.current_pair ?? {}) as Record<string, unknown>;
  const profiles = currentPair.profile_sets || currentPair.profileSets || currentPair.candidate_profiles || currentPair.candidateProfiles || currentPair.profiles || currentPair.archetypes;
  return Array.isArray(profiles) ? profiles.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object")) : [];
}

function getCalibrationCurrentArchetypes(calibration: RecruiterIntelligenceSession["calibration"] | null | undefined): Array<Record<string, unknown>> {
  return getCalibrationCurrentProfiles(calibration);
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
  const totalSets = calibration.profile_sets || calibration.candidate_profile_sets || calibration.archetype_sets;
  const total = Array.isArray(totalSets) ? totalSets.length || 3 : 3;
  return `${current} / ${total}`;
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
  const topSkills = formatList(getCandidateSkills(candidate), "Not provided").slice(0, 6);
  const role = getCandidateCurrentRole(candidate) || candidate.role || candidate.headline || "Not provided";
  const linkedInUrl = getCandidateLinkedInUrl(candidate);
  const safeLocation = getCandidateLocation(candidate);
  const rawDiscovery = getCandidateRawDiscovery(candidate);
  const sourceUrl = String(candidate.sourceUrl || candidate.source_url || rawDiscovery.source_url || "").trim();
  const sourceQuery = String(candidate.sourceQuery || rawDiscovery.query || "").trim();
  const sourceTitle = String(rawDiscovery.title || candidate.name || "").trim();
  const sourceSnippet = String(rawDiscovery.snippet || candidate.summary || "").trim();
  const displayLink = String(rawDiscovery.displayed_link || "").trim();
  const sourceProvider = String(candidate.sourceProvider || rawDiscovery.source_provider || "").trim();
  const currentCompany = String(candidate.currentCompany || candidate.company || rawDiscovery.current_company || "").trim();
  const experienceLabel = candidate.yearsExperience ? `${candidate.yearsExperience.toFixed(1)} years` : String(candidate.inferredExperience || rawDiscovery.inferred_experience || "").trim();
  const profileSummary = sourceSnippet || candidate.summary || candidate.headline || "";

  return (
    <div className="space-y-4">
      {linkedInUrl && (
        <div className="flex justify-end">
          <a
            href={linkedInUrl}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => event.stopPropagation()}
            aria-label="Open LinkedIn profile"
            className="inline-flex items-center gap-1.5 rounded-full border border-[#D8E6DF] bg-[#EEF7F1] px-4 py-2 text-xs font-semibold text-[#0F6B3A] transition hover:bg-[#E4F2EA]"
          >
            LinkedIn
            <ArrowUpRight className="h-3.5 w-3.5" />
          </a>
        </div>
      )}

      <div className="grid gap-3 rounded-[18px] border border-[#ECE7DE] bg-[#F8F7F3] p-4 md:grid-cols-2">
        {[
          candidate.name ? ["Name", candidate.name] : null,
          role ? ["Current role", role] : null,
          currentCompany ? ["Current company", currentCompany] : null,
          safeLocation ? ["Location", safeLocation] : null,
          experienceLabel ? ["Experience", experienceLabel] : null,
          candidate.sourceProvider ? ["Source", candidate.sourceProvider === "xray_apollo" ? "LinkedIn x-ray" : candidate.sourceProvider] : null,
        ]
          .filter((item): item is [string, string] => Boolean(item))
          .map(([label, value]) => (
            <DetailRow key={`${candidate.id}-${label}`} label={label} value={value} />
          ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-4">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Top skills</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {topSkills.map((item) => (
              <span key={`skill-${candidate.id}-${item}`} className="rounded-full bg-[#F4FBF7] px-3 py-1 text-xs font-semibold text-[#0F6B3A]">
                {item}
              </span>
            ))}
          </div>
        </div>

        <div className="rounded-[18px] border border-[#ECE7DE] bg-white p-4">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#1D4ED8]">Source details</p>
          <div className="mt-3 space-y-2 text-sm text-[#4B5563]">
            {sourceTitle && <p><span className="font-semibold text-[#111827]">Title:</span> {sourceTitle}</p>}
            {displayLink && <p><span className="font-semibold text-[#111827]">Display:</span> {displayLink}</p>}
            {sourceUrl && <p className="break-all"><span className="font-semibold text-[#111827]">URL:</span> {sourceUrl}</p>}
            {sourceQuery && <p><span className="font-semibold text-[#111827]">Query:</span> {trimText(sourceQuery, 180)}</p>}
            {rawDiscovery.page != null && <p><span className="font-semibold text-[#111827]">Page:</span> {String(rawDiscovery.page)}</p>}
            {rawDiscovery.position != null && <p><span className="font-semibold text-[#111827]">Position:</span> {String(rawDiscovery.position)}</p>}
          </div>
        </div>
      </div>

      {profileSummary && (
        <div className="rounded-[18px] border border-[#ECE7DE] bg-[#F8F7F3] p-4">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Summary</p>
          <p className="mt-3 font-body text-[13px] leading-6 text-[#4B5563]">{trimText(profileSummary, 420)}</p>
        </div>
      )}

      <div className="rounded-[18px] border border-[#E5E7EB] bg-white p-4">
        <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#1D4ED8]">Why ranked here</p>
        <p className="mt-3 font-body text-[13px] leading-6 text-[#374151]">{trimText(getReasoningSummary(candidate), 420)}</p>
        <div className="mt-3 inline-flex rounded-full bg-[#EEF7FF] px-3 py-1 text-[11px] font-semibold text-[#1D4ED8]">
          {getSnippetQualityLabel(candidate)}
        </div>
      </div>
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
      onClick={(event) => {
        if (isInteractiveElement(event.target)) return;
        onOpenDetails();
      }}
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
              {candidate.name || "Unnamed candidate"}
            </CardTitle>
            <div className="space-y-2 font-body text-[14px] leading-5 text-[#4B5563]">
              <p className="flex items-center gap-2">
                <BriefcaseBusiness className="h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span style={clampLines(2)}>
                  {getCandidateCurrentRole(candidate) || candidate.role || candidate.headline || "Not provided"}
                </span>
              </p>
              <p className="flex items-center gap-2">
                <Building2 className="h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span>{candidate.company || "Not provided"}</span>
              </p>
              <p className="flex items-center gap-2">
                <MapPin className="h-4 w-4 shrink-0 text-[#0F6B3A]" />
                <span>{getCandidateLocation(candidate) || "Not provided"}</span>
              </p>
            </div>
          </div>
          <div className="flex flex-col items-end gap-2">
            {getCandidateLinkedInUrl(candidate) && (
              <a
                href={getCandidateLinkedInUrl(candidate)}
                target="_blank"
                rel="noreferrer"
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => event.stopPropagation()}
                aria-label="Open LinkedIn profile"
                className="inline-flex items-center gap-1.5 rounded-full border border-[#D8E6DF] bg-[#EEF7F1] px-3 py-2 text-[11px] font-semibold text-[#0F6B3A] transition hover:bg-[#E4F2EA]"
              >
                LinkedIn
                <ArrowUpRight className="h-3.5 w-3.5" />
              </a>
            )}
            <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-[#DDF5E6] text-center font-body text-[14px] font-semibold leading-tight text-[#0F6B3A]">
              <span>Ranked</span>
            </div>
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex flex-1 flex-col p-8 pt-6">
        <div className="mb-4 flex flex-wrap gap-2">
          <span className="inline-flex rounded-full bg-[#F3F4F6] px-3 py-1 font-body text-[11px] font-semibold text-[#6B7280]">
            {statusLabel(candidate)}
          </span>
          <span className="inline-flex rounded-full bg-[#EEF7FF] px-3 py-1 font-body text-[11px] font-semibold text-[#1D4ED8]">
            {candidate.sourceProvider === "xray_apollo" ? "LinkedIn x-ray" : candidate.sourceProvider || "Source pending"}
          </span>
          <span className="inline-flex rounded-full bg-[#F4FBF7] px-3 py-1 font-body text-[11px] font-semibold text-[#0F6B3A]">
            {candidate.enrichmentStatus === "pending"
              ? "Not enriched yet"
              : candidate.enrichmentStatus === "enriching"
                ? "Enriching"
                : candidate.enrichmentStatus || "Pending"}
          </span>
          <span className="inline-flex rounded-full bg-[#EEF7FF] px-3 py-1 font-body text-[11px] font-semibold text-[#1D4ED8]">
            {getSnippetQualityLabel(candidate)}
          </span>
        </div>

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
            <Sparkles className="h-4 w-4 shrink-0 text-[#6B7280]" />
            <span className="font-body text-[14px] text-[#4B5563]">Skills</span>
            <span className="ml-auto font-body text-[14px] font-semibold text-[#111827]">
              {getCandidateSkills(candidate).slice(0, 3).join(", ") || "Not provided"}
            </span>
          </div>
          <div className="flex items-center gap-3 border-t border-[#ECE7DE] py-2">
            <Sparkles className="h-4 w-4 shrink-0 text-[#6B7280]" />
            <span className="font-body text-[14px] text-[#4B5563]">Source</span>
            <span className="ml-auto font-body text-[14px] font-semibold text-[#111827]">
              {candidate.sourceProvider === "xray_apollo" ? "LinkedIn x-ray" : candidate.sourceProvider || "Pending"}
            </span>
          </div>
        </div>

        <div className="mt-4 rounded-[18px] border border-[#E5E7EB] bg-white p-4">
          <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#1D4ED8]">Why ranked here</p>
          <p className="mt-2 font-body text-[13px] leading-6 text-[#374151]">{trimText(getReasoningSummary(candidate), 320)}</p>
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
      onClick={(event) => {
        if (isInteractiveElement(event.target)) return;
        onOpenDetails();
      }}
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
              {candidate.name || "Unnamed candidate"}
            </h3>
            <span className="text-[#A18E7C] transition-opacity group-hover:text-[#7D6A57]">â†—</span>
          </div>
          <p className="font-body text-[14px] text-[#8A6F55]">{candidateSubtitle(candidate)}</p>
          <p style={clampLines(3)} className="max-w-4xl overflow-hidden font-body text-[15px] leading-6 text-[#5F564D]">
            {trimText(candidate.summary || candidate.headline || candidate.role || "No summary provided", 260)}
          </p>
          <p className="mt-2 font-body text-[13px] leading-6 text-[#374151]">{trimText(getReasoningSummary(candidate), 220)}</p>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-2 pl-2 text-right">
          <div className="rounded-full bg-[#EEF7FF] px-3 py-1 text-[12px] font-semibold text-[#1D4ED8]">{getSnippetQualityLabel(candidate)}</div>
          <div className="rounded-full bg-[#F4FBF7] px-3 py-1 text-[12px] font-semibold text-[#0F6B3A]">Ranked</div>
        </div>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        {getCandidateLocation(candidate) && (
          <span className="rounded-full bg-[#F7F3EB] px-3 py-1 text-[12px] font-medium text-[#7D6A57]">
            {getCandidateLocation(candidate)}
          </span>
        )}
        {candidate.company && (
          <span className="rounded-full bg-[#F7F3EB] px-3 py-1 text-[12px] font-medium text-[#7D6A57]">
            {candidate.company}
          </span>
        )}
        {getCandidateSkills(candidate).slice(0, 3).map((skill) => (
          <span key={`${candidate.id}-${skill}`} className="rounded-full bg-[#F4FBF7] px-3 py-1 text-[12px] font-medium text-[#0F6B3A]">
            {skill}
          </span>
        ))}
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

// â”€â”€ Tinder-style swipe deck for final candidates â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function SwipeDeck({
  candidates,
  shortlistedIds,
  shortlistedCandidates,
  isAdvancing,
  selectedCandidateId,
  onSelect,
  onReject,
  onOpenDetails,
  onContinueToReady,
}: {
  candidates: Candidate[];
  shortlistedIds: string[];
  shortlistedCandidates: Candidate[];
  isAdvancing: boolean;
  selectedCandidateId: string;
  onSelect: (id: string) => void;
  onReject: (id: string) => void;
  onOpenDetails: (c: Candidate) => void;
  onContinueToReady: () => void;
}) {
  const [swipeDir, setSwipeDir] = useState<"left" | "right" | null>(null);
  const [swipingId, setSwipingId] = useState("");

  // Drag state
  const dragStartX = useRef(0);
  const dragCurrentX = useRef(0);
  const isDragging = useRef(false);
  const [dragOffset, setDragOffset] = useState(0);

  const current = candidates[0] ?? null;
  const next = candidates[1] ?? null;

  const done = candidates.length === 0;

  const triggerSwipe = (id: string, dir: "left" | "right") => {
    if (isAdvancing || swipingId) return;
    setSwipingId(id);
    setSwipeDir(dir);
    setTimeout(() => {
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
            {candidates.length} remaining · {shortlistedCandidates.length} shortlisted
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
            onClick={(event) => {
              if (isInteractiveElement(event.target)) return;
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
                    {current.name || "Unnamed candidate"}
                  </h3>
                  <p className="font-body text-[14px] text-[#4B5563]">
                    {getCandidateCurrentRole(current) || current.role || current.headline || "Not provided"}
                    {(current.currentCompany || current.company) ? ` @ ${current.currentCompany || current.company}` : ""}
                  </p>
                  {getCandidateLocation(current) && (
                    <p className="flex items-center gap-1 font-body text-[13px] text-[#9CA3AF]">
                      <MapPin className="h-3.5 w-3.5" />
                      {getCandidateLocation(current)}
                    </p>
                  )}
                </div>
                <div className="flex flex-col items-end gap-2">
                  {getCandidateLinkedInUrl(current) && (
                    <a
                      href={getCandidateLinkedInUrl(current)}
                      target="_blank"
                      rel="noreferrer"
                      onPointerDown={(event) => event.stopPropagation()}
                      onMouseDown={(event) => event.stopPropagation()}
                      onClick={(event) => event.stopPropagation()}
                      aria-label="Open LinkedIn profile"
                      className="inline-flex items-center gap-1.5 rounded-full border border-[#D8E6DF] bg-[#EEF7F1] px-3 py-2 text-[11px] font-semibold text-[#0F6B3A] transition hover:bg-[#E4F2EA]"
                    >
                      LinkedIn
                      <ArrowUpRight className="h-3.5 w-3.5" />
                    </a>
                  )}
                </div>
              </div>

              {/* Summary */}
              {current.summary && (
                <p className="mb-4 line-clamp-3 font-body text-[14px] leading-6 text-[#5F564D]">
                  {current.summary}
                </p>
              )}

              <div className="mb-4 rounded-[18px] border border-[#E5E7EB] bg-white p-4">
                <p className="font-body text-[11px] font-semibold uppercase tracking-[0.18em] text-[#1D4ED8]">Why ranked here</p>
                <p className="mt-2 font-body text-[13px] leading-6 text-[#374151]">{trimText(getReasoningSummary(current), 260)}</p>
                <div className="mt-3 inline-flex rounded-full bg-[#EEF7FF] px-3 py-1 text-[11px] font-semibold text-[#1D4ED8]">
                  {getSnippetQualityLabel(current)}
                </div>
              </div>

              {/* Skills */}
              {getCandidateSkills(current).length > 0 && (
                <div className="mb-4 flex flex-wrap gap-2">
                  {getCandidateSkills(current).slice(0, 5).map((skill) => (
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
              {(shortlistedIds.includes(current.id) || isShortlistedStatus(current.status) || isShortlistedStatus(current.ats_status)) && (
                <div className="mb-3 rounded-full bg-[#DDF5E6] px-4 py-1.5 text-center font-body text-[13px] font-semibold text-[#0F6B3A]">
                  âœ“ Already shortlisted
                </div>
              )}

              {/* Action buttons */}
              <div className="mt-auto flex gap-3">
                <button
                  type="button"
                  onPointerDown={(e) => e.stopPropagation()}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); triggerSwipe(current.id, "left"); }}
                  disabled={isAdvancing || Boolean(swipingId) || (selectedCandidateId !== "" && selectedCandidateId !== current.id)}
                  className="flex h-14 flex-1 items-center justify-center gap-2 rounded-[16px] border-2 border-[#FCA5A5] bg-white font-body text-[15px] font-semibold text-[#DC2626] transition hover:bg-[#FEF2F2] disabled:opacity-50"
                >
                  <CircleX className="h-5 w-5" /> Pass
                </button>
                <button
                  type="button"
                  onPointerDown={(e) => e.stopPropagation()}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); triggerSwipe(current.id, "right"); }}
                  disabled={isAdvancing || Boolean(swipingId) || (selectedCandidateId !== "" && selectedCandidateId !== current.id)}
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
              shortlistedCandidates.some((item) => item.id === c.id) || shortlistedIds.includes(c.id) || isShortlistedStatus(c.status) || isShortlistedStatus(c.ats_status)
                ? "bg-[#0F6B3A]"
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

function RecruiterSwipeDeck({
  candidates,
  shortlistedIds,
  shortlistedCandidates,
  isAdvancing,
  selectedCandidateId,
  onSelect,
  onReject,
  onOpenDetails,
  onContinueToReady,
}: {
  candidates: Candidate[];
  shortlistedIds: string[];
  shortlistedCandidates: Candidate[];
  isAdvancing: boolean;
  selectedCandidateId: string;
  onSelect: (id: string) => void;
  onReject: (id: string) => void;
  onOpenDetails: (c: Candidate) => void;
  onContinueToReady: () => void;
}) {
  const [swipeDir, setSwipeDir] = useState<"left" | "right" | null>(null);
  const [swipingId, setSwipingId] = useState("");
  const dragStartX = useRef(0);
  const dragCurrentX = useRef(0);
  const isDragging = useRef(false);
  const [dragOffset, setDragOffset] = useState(0);

  const current = candidates[0] ?? null;
  const next = candidates[1] ?? null;

  const triggerSwipe = (id: string, dir: "left" | "right") => {
    if (isAdvancing || swipingId) return;
    setSwipingId(id);
    setSwipeDir(dir);
    setTimeout(() => {
      setSwipeDir(null);
      setSwipingId("");
      setDragOffset(0);
      if (dir === "right") onSelect(id);
      else onReject(id);
    }, 280);
  };

  const onPointerDown = (event: React.PointerEvent) => {
    if (!current || isAdvancing || swipingId) return;
    isDragging.current = true;
    dragStartX.current = event.clientX;
    dragCurrentX.current = event.clientX;
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent) => {
    if (!isDragging.current || !current) return;
    dragCurrentX.current = event.clientX;
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

  if (candidates.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 rounded-[24px] border border-[#E7E0D4] bg-white py-16 text-center shadow-[0_8px_24px_rgba(0,0,0,0.04)]">
        <p className="font-heading text-[22px] font-semibold text-[#111827]">All candidates reviewed</p>
        <p className="font-body text-sm text-[#6B7280]">We surfaced the ranked pool directly, so recruiters can review the full high-signal set.</p>
        <Button
          className="rounded-[14px] bg-[#0F6B3A] px-5 py-2 text-[15px] font-semibold text-white hover:bg-[#0C5A31]"
          onClick={(event) => {
            event.stopPropagation();
            onContinueToReady();
          }}
          disabled={shortlistedCandidates.length === 0 || isAdvancing}
        >
          Move to Ready
        </Button>
      </div>
    );
  }

  const rotation = Math.min(Math.max(dragOffset / 20, -12), 12);
  const overlayOpacity = Math.min(Math.abs(dragOffset) / 120, 1);
  const isRight = dragOffset > 0;
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
      <div className="flex items-center justify-between gap-3">
        <div className="space-y-1">
          <p className="font-heading text-[22px] font-semibold text-[#111827]">Swipe to shortlist</p>
          <p className="font-body text-sm text-[#8A6F55]">
            {candidates.length} remaining · {shortlistedCandidates.length} shortlisted
          </p>
        </div>
      </div>

      <p className="text-center font-body text-xs text-[#9CA3AF]">Swipe right to shortlist · Swipe left to reject · Tap for details</p>

      <div className="relative mx-auto h-[520px] w-full max-w-[420px] select-none">
        {next && <div className="absolute inset-0 scale-[0.96] rounded-[28px] border border-[#E7E0D4] bg-white shadow-[0_4px_16px_rgba(0,0,0,0.06)]" />}

        {current && (
          <div
            className="absolute inset-0 cursor-grab rounded-[28px] border border-[#E7E0D4] bg-white shadow-[0_12px_32px_rgba(0,0,0,0.10)] active:cursor-grabbing"
            style={{
              transform: swipeTransform,
              transition: swipingId === current.id ? "transform 0.28s cubic-bezier(0.4,0,0.2,1)" : dragOffset !== 0 ? "none" : "transform 0.2s ease",
            }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onClick={(event) => {
              if (isInteractiveElement(event.target)) return;
              if (Math.abs(dragOffset) < 5) onOpenDetails(current);
            }}
          >
            {dragOffset !== 0 && (
              <>
                <div className="pointer-events-none absolute inset-0 rounded-[28px] bg-[#DDF5E6]" style={{ opacity: isRight ? overlayOpacity * 0.55 : 0 }} />
                <div className="pointer-events-none absolute inset-0 rounded-[28px] bg-[#FEE2E2]" style={{ opacity: !isRight ? overlayOpacity * 0.55 : 0 }} />
                <div
                  className="pointer-events-none absolute left-5 top-6 rounded-xl border-4 border-[#0F6B3A] px-4 py-2 font-heading text-[20px] font-bold text-[#0F6B3A]"
                  style={{ opacity: isRight ? overlayOpacity : 0, transform: "rotate(-15deg)" }}
                >
                  SHORTLIST
                </div>
                <div
                  className="pointer-events-none absolute right-5 top-6 rounded-xl border-4 border-[#DC2626] px-4 py-2 font-heading text-[20px] font-bold text-[#DC2626]"
                  style={{ opacity: !isRight ? overlayOpacity : 0, transform: "rotate(15deg)" }}
                >
                  REJECT
                </div>
              </>
            )}

            <div className="flex h-full flex-col p-7">
              <div className="mb-4 flex items-start justify-between gap-4">
                <div className="space-y-2">
                  <h3 className="font-heading text-[26px] font-bold leading-tight text-[#111827]">{current.name || current.id.slice(0, 8)}</h3>
                  <p className="font-body text-[14px] text-[#4B5563]">
                    {getCandidateCurrentRole(current) || current.role || current.headline || "Not provided"}
                    {(current.currentCompany || current.company) ? ` @ ${current.currentCompany || current.company}` : ""}
                  </p>
                  {getCandidateLocation(current) && (
                    <p className="flex items-center gap-1 font-body text-[13px] text-[#9CA3AF]">
                      <MapPin className="h-3.5 w-3.5" />
                      {getCandidateLocation(current)}
                    </p>
                  )}
                </div>
                <div className="flex flex-col items-end gap-2">
                  {getCandidateLinkedInUrl(current) && (
                    <a
                      href={getCandidateLinkedInUrl(current)}
                      target="_blank"
                      rel="noreferrer"
                      onPointerDown={(event) => event.stopPropagation()}
                      onMouseDown={(event) => event.stopPropagation()}
                      onClick={(event) => event.stopPropagation()}
                      aria-label="Open LinkedIn profile"
                      className="inline-flex items-center gap-1.5 rounded-full border border-[#D8E6DF] bg-[#EEF7F1] px-3 py-2 text-[11px] font-semibold text-[#0F6B3A] transition hover:bg-[#E4F2EA]"
                    >
                      LinkedIn
                      <ArrowUpRight className="h-3.5 w-3.5" />
                    </a>
                  )}
                </div>
              </div>

              {getCandidateSkills(current).length > 0 && (
                <div className="mb-4 flex flex-wrap gap-2">
                  {getCandidateSkills(current).slice(0, 4).map((skill) => (
                    <span key={`${current.id}-${skill}`} className="rounded-full bg-[#F4FBF7] px-3 py-1 text-[12px] font-medium text-[#0F6B3A]">
                      {skill}
                    </span>
                  ))}
                </div>
              )}

              <div className="grid gap-2 rounded-[18px] border border-[#ECE7DE] bg-[#FBFAF7] p-4 text-sm text-[#4B5563]">
                <p>
                  <span className="font-semibold text-[#111827]">Current role:</span>{" "}
                  {getCandidateCurrentRole(current) || current.role || current.headline || "Not provided"}
                </p>
                <p>
                  <span className="font-semibold text-[#111827]">Current company:</span> {current.currentCompany || current.company || "Not provided"}
                </p>
                <p>
                  <span className="font-semibold text-[#111827]">Location:</span> {getCandidateLocation(current) || "Not provided"}
                </p>
                <p>
                  <span className="font-semibold text-[#111827]">Skills:</span> {getCandidateSkills(current).slice(0, 4).join(", ") || "Not provided"}
                </p>
              </div>

              {(shortlistedIds.includes(current.id) || isShortlistedStatus(current.status) || isShortlistedStatus(current.ats_status)) && (
                <div className="mb-3 rounded-full bg-[#DDF5E6] px-4 py-1.5 text-center font-body text-[13px] font-semibold text-[#0F6B3A]">
                  Already shortlisted
                </div>
              )}

              <div className="mt-auto flex gap-3">
                <button
                  type="button"
                  onPointerDown={(event) => event.stopPropagation()}
                  onMouseDown={(event) => event.stopPropagation()}
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    triggerSwipe(current.id, "left");
                  }}
                  disabled={isAdvancing || Boolean(swipingId)}
                  className="flex h-14 flex-1 items-center justify-center gap-2 rounded-[16px] border-2 border-[#FCA5A5] bg-white font-body text-[15px] font-semibold text-[#DC2626] transition hover:bg-[#FEF2F2] disabled:opacity-50"
                >
                  <CircleX className="h-5 w-5" /> Reject
                </button>
                <button
                  type="button"
                  onPointerDown={(event) => event.stopPropagation()}
                  onMouseDown={(event) => event.stopPropagation()}
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    triggerSwipe(current.id, "right");
                  }}
                  disabled={isAdvancing || Boolean(swipingId)}
                  className="flex h-14 flex-1 items-center justify-center gap-2 rounded-[16px] bg-[#0F6B3A] font-body text-[15px] font-semibold text-white shadow-[0_6px_16px_rgba(15,107,58,0.22)] transition hover:bg-[#0C5A31] disabled:opacity-50"
                >
                  <CheckCircle2 className="h-5 w-5" /> Shortlist
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="flex justify-center gap-1.5">
        {candidates.map((candidate) => (
          <div
            key={candidate.id}
            className={`h-2 w-2 rounded-full transition-colors ${
              shortlistedCandidates.some((item) => item.id === candidate.id) || shortlistedIds.includes(candidate.id) || isShortlistedStatus(candidate.status) || isShortlistedStatus(candidate.ats_status)
                ? "bg-[#0F6B3A]"
                : candidate.id === current?.id
                  ? "bg-[#111827]"
                  : "bg-[#E5E7EB]"
            }`}
          />
        ))}
      </div>
    </div>
  );
}

function RecruiterCandidateModal({
  candidate,
  open,
  onClose,
  onReject,
  onShortlist,
  isAdvancing,
  selectedCandidateId,
}: {
  candidate: Candidate | null;
  open: boolean;
  onClose: () => void;
  onReject: () => void;
  onShortlist: () => void;
  isAdvancing: boolean;
  selectedCandidateId: string;
}) {
  return (
    <Modal
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onClose();
      }}
      title={candidate?.name || "Candidate profile"}
      description={candidate ? `${getCandidateCurrentRole(candidate) || candidate.role || candidate.headline || "Not provided"}${candidate.company ? ` @ ${candidate.company}` : ""}` : ""}
      className="max-w-4xl max-h-[90vh] overflow-y-auto"
    >
      {candidate && (
        <div className="space-y-5">
          <CandidateDetails candidate={candidate} />
          <div className="flex flex-col gap-3 md:flex-row">
            <Button
              variant="outline"
              className="w-full justify-center rounded-[14px] border-[#FCA5A5] bg-white text-red-700 hover:bg-red-50 md:w-auto md:flex-1"
              onClick={(event) => {
                event.stopPropagation();
                onReject();
              }}
              disabled={isAdvancing || (selectedCandidateId !== "" && selectedCandidateId !== candidate.id)}
            >
              Reject
            </Button>
            <Button
              className="w-full justify-center rounded-[14px] bg-[#0F6B3A] text-[16px] font-semibold text-white hover:bg-[#0C5A31] md:w-auto md:flex-1"
              onClick={(event) => {
                event.stopPropagation();
                onShortlist();
              }}
                  disabled={isAdvancing || selectedCandidateId !== "" || isShortlistedStatus(candidate.status) || isShortlistedStatus(candidate.ats_status)}
                >
                  {isAdvancing && selectedCandidateId === candidate.id
                    ? "Shortlisting..."
                    : isShortlistedStatus(candidate.status) || isShortlistedStatus(candidate.ats_status)
                      ? "Shortlisted"
                      : "Shortlist"}
                </Button>
            <Button
              variant="outline"
              className="w-full justify-center rounded-[14px] border-[#E7E0D4] bg-white md:w-auto"
              onClick={onClose}
            >
              Close
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

export default function ReviewPage() {
  const router = useRouter();
  const { user, isSessionReady, jobId, isRefined } = useAppContext();

  const [session, setSession] = useState<CandidateSelectionSession | null>(null);
  const [intelligence, setIntelligence] = useState<RecruiterIntelligenceSession | null>(null);
  const [reviewCandidates, setReviewCandidates] = useState<Candidate[]>([]);
  const [remainingCandidates, setRemainingCandidates] = useState<Candidate[]>([]);
  const [shortlistedCandidates, setShortlistedCandidates] = useState<Candidate[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [isCalibrationLoading, setIsCalibrationLoading] = useState(false);
  const [isAdvancing, setIsAdvancing] = useState(false);
  const [isContinuingToReady, setIsContinuingToReady] = useState(false);
  const [error, setError] = useState("");
  const [calibrationError, setCalibrationError] = useState("");
  const [sourcingError, setSourcingError] = useState("");
  const [calibrationSelectionId, setCalibrationSelectionId] = useState("");
  const [feedbackMessage, setFeedbackMessage] = useState("");
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
  const sourcingLoadKeyRef = useRef("");
  const sourcingRefreshCycleRef = useRef(0);
  const sourcingInFlightRef = useRef(false);
  const candidateStateVersionRef = useRef(0);

  useEffect(() => {
    if (!jobId) return;
    setFinalShortlistedIds(getStoredShortlistedCandidateIds(jobId));
  }, [jobId]);

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

  const loadSourcedCandidates = async (options?: { source?: string; forceRefresh?: boolean }) => {
    if (!isSessionReady || !user || !jobId) return;
    const source = options?.source || "auto";
    const forceRefresh = Boolean(options?.forceRefresh);
    const loadKey = `${jobId}:${user.id}:${source}:${forceRefresh ? "refresh" : "steady"}:${sourcingRefreshCycleRef.current}`;
    const loadVersion = candidateStateVersionRef.current;
    if (sourcingInFlightRef.current) return;
    if (!forceRefresh && sourcingLoadKeyRef.current === loadKey) return;
    sourcingInFlightRef.current = true;
    sourcingLoadKeyRef.current = loadKey;
    setReviewLoading(true);
    setSourcingError("");
    const persistedShortlistedIds = new Set<string>([
      ...getStoredShortlistedCandidateIds(jobId),
      ...finalShortlistedIds,
    ]);

    const cachedCandidates = getStoredReviewCandidates(jobId);
    const persistedRejectedIds = new Set<string>(
      cachedCandidates
        .filter((candidate) => String(candidate.status || "").trim().toLowerCase() === "rejected" || String(candidate.ats_status || "").trim().toLowerCase() === "rejected")
        .map((candidate) => candidate.id)
    );
    let shouldAbortLoad = false;
    if (cachedCandidates.length > 0) {
      if (candidateStateVersionRef.current === loadVersion) {
        const normalizedCachedCandidates = cachedCandidates.slice(0, 30).map((candidate) =>
          persistedRejectedIds.has(candidate.id)
            ? { ...candidate, status: "rejected" as const }
            : persistedShortlistedIds.has(candidate.id) || isShortlistedStatus(candidate.status)
            ? { ...candidate, status: "selected" as const }
            : candidate
        );
        setReviewCandidates(normalizedCachedCandidates);
        setRemainingCandidates(
          normalizedCachedCandidates.filter(
            (candidate) => !persistedShortlistedIds.has(candidate.id) && !persistedRejectedIds.has(candidate.id) && candidate.status !== "rejected"
          )
        );
        setShortlistedCandidates(
          normalizedCachedCandidates.filter((candidate) => persistedShortlistedIds.has(candidate.id) || isShortlistedStatus(candidate.status))
        );
        if (!forceRefresh) {
          setFeedbackMessage(`Restored ${cachedCandidates.length} cached candidate${cachedCandidates.length === 1 ? "" : "s"} for this role.`);
        }
      } else {
        shouldAbortLoad = true;
      }
    }

    if (shouldAbortLoad) {
      setReviewLoading(false);
      sourcingInFlightRef.current = false;
      return;
    }

    try {
      const result = await getCandidates({
        jobId,
        refined: true,
      });

      if (!result.success || !result.data) {
        const message = result.error || "Could not load sourced candidates.";
        setSourcingError(message);
        if (cachedCandidates.length === 0) {
          setReviewCandidates([]);
        }
        return;
      }

      if (candidateStateVersionRef.current !== loadVersion) {
        return;
      }

      const rankedCandidates = result.data.slice(0, 30);
      const normalizedRankedCandidates = rankedCandidates.map((candidate) =>
        persistedRejectedIds.has(candidate.id) || String(candidate.status || "").trim().toLowerCase() === "rejected" || String(candidate.ats_status || "").trim().toLowerCase() === "rejected"
          ? { ...candidate, status: "rejected" as const }
          : persistedShortlistedIds.has(candidate.id) || isShortlistedStatus(candidate.status) || isShortlistedStatus(candidate.ats_status)
          ? { ...candidate, status: "selected" as const }
          : candidate
      );
      const shortlistedIdsFromRanked = normalizedRankedCandidates
        .filter((candidate) => isShortlistedStatus(candidate.status) || isShortlistedStatus(candidate.ats_status))
        .map((candidate) => candidate.id);
      const mergedShortlistedIds = Array.from(new Set([...persistedShortlistedIds, ...shortlistedIdsFromRanked]));
      setFinalShortlistedIds(mergedShortlistedIds);
      setReviewCandidates(normalizedRankedCandidates);
      setRemainingCandidates(
        normalizedRankedCandidates.filter(
          (candidate) =>
            !mergedShortlistedIds.includes(candidate.id) &&
            !persistedRejectedIds.has(candidate.id) &&
            String(candidate.status || "").trim().toLowerCase() !== "rejected" &&
            String(candidate.ats_status || "").trim().toLowerCase() !== "rejected" &&
            !isShortlistedStatus(candidate.status) &&
            !isShortlistedStatus(candidate.ats_status)
        )
      );
      setShortlistedCandidates(
        normalizedRankedCandidates.filter((candidate) => mergedShortlistedIds.includes(candidate.id) || isShortlistedStatus(candidate.status) || isShortlistedStatus(candidate.ats_status))
      );
      storeReviewCandidates(jobId, normalizedRankedCandidates);
      storeShortlistedCandidateIds(jobId, mergedShortlistedIds);
      setFeedbackMessage(
        rankedCandidates.length > 0
          ? `X-Ray sourcing loaded ${rankedCandidates.length} ranked candidate${rankedCandidates.length === 1 ? "" : "s"} for recruiter review.`
          : "X-Ray sourcing completed, but no candidates were returned."
      );
    } finally {
      setReviewLoading(false);
      sourcingInFlightRef.current = false;
    }
  };

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
        setCalibrationError(intelligenceResult.error || "Could not load candidate profiles.");
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
        setReviewCandidates([]);
        setRemainingCandidates([]);
        setShortlistedCandidates([]);
        setSourcingError("");
        setIsLoading(false);
        return;
      }

      if (calibrationReady) {
        const cachedCandidates = getStoredReviewCandidates(jobId);
        if (cachedCandidates.length > 0) {
          setReviewCandidates(cachedCandidates.slice(0, 30));
          setFeedbackMessage(`Restored ${cachedCandidates.length} cached candidate${cachedCandidates.length === 1 ? "" : "s"} for this role.`);
        }
        await loadSourcedCandidates({ source: "auto", forceRefresh: false });
      } else {
        setReviewCandidates([]);
        setRemainingCandidates([]);
        setShortlistedCandidates([]);
        setSourcingError("");
      }
      if (cancelled) return;
      setActiveCandidate(null);
      setIsLoading(false);
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [isSessionReady, jobId, user?.id]);

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

  const finalCandidates = reviewCandidates;
  const analysis = null;
  const summaryLines = useMemo(() => analysisSummary(analysis), [analysis]);
  const calibration = intelligence?.calibration ?? null;
  const calibrationArchetypes = useMemo(() => getCalibrationCurrentProfiles(calibration), [calibration]);
  const calibrationRoundLabel = useMemo(() => getCalibrationRoundLabel(calibration), [calibration]);
  const calibrationSetId = useMemo(() => getCalibrationCurrentSetId(calibration), [calibration]);
  const calibrationComplete = calibration?.stage === "real_sourcing_ready";
  const interviewProgression = activeInterviewInsights?.progression || [];
  const activeInterviewStage = interviewProgression.find((item: any) => item?.active) || interviewProgression[0] || null;
  const completedInterviewStages = interviewProgression.filter((item: any) => item?.completed).length;
  const swipeCandidates = useMemo(
    () =>
      reviewCandidates.filter(
        (candidate) =>
          candidate.status !== "rejected" &&
          candidate.ats_status !== "rejected" &&
          !isShortlistedStatus(candidate.status) &&
          !isShortlistedStatus(candidate.ats_status) &&
          !finalShortlistedIds.includes(candidate.id)
      ),
    [finalShortlistedIds, reviewCandidates]
  );
  const visibleShortlistedCandidates = useMemo(
    () =>
      reviewCandidates.filter(
        (candidate) =>
          finalShortlistedIds.includes(candidate.id) ||
          isShortlistedStatus(candidate.status) ||
          isShortlistedStatus(candidate.ats_status)
      ),
    [finalShortlistedIds, reviewCandidates]
  );
  const completedShortlistedIds = useMemo(() => {
    const ids = new Set<string>(finalShortlistedIds);
    for (const candidate of reviewCandidates) {
      if (isShortlistedStatus(candidate.status) || isShortlistedStatus(candidate.ats_status)) {
        ids.add(candidate.id);
      }
    }
    for (const candidateId of getStoredShortlistedCandidateIds(jobId || "")) {
      if (candidateId) ids.add(candidateId);
    }
    return [...ids];
  }, [finalShortlistedIds, reviewCandidates, jobId]);
  const shortlistedCount = useMemo(() => {
    return visibleShortlistedCandidates.length || completedShortlistedIds.length;
  }, [completedShortlistedIds.length, visibleShortlistedCandidates.length]);

  const handleCalibrationSelect = async (archetypeId: string) => {
    if (!jobId || !user || isCalibrationLoading) return;
    setIsCalibrationLoading(true);
    setCalibrationError("");
    setFeedbackMessage("");
    setCalibrationSelectionId(archetypeId);

    const result = await chooseRecruiterCalibrationArchetype(user.id, jobId, {
      jobId,
      candidateId: archetypeId,
      calibrationSetId,
    });

    const calibrationResult = result.data ?? null;
    if (!result.success || !calibrationResult) {
      setCalibrationError(result.error || "Could not save candidate profile choice.");
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
        await loadSourcedCandidates({ source: "calibration", forceRefresh: false });
      }

    setCalibrationSelectionId("");
    setIsCalibrationLoading(false);
  };

  const handleSelect = async (candidateId: string) => {
    if (!jobId || isAdvancing) return;
    setIsAdvancing(true);
    setError("");
    setSelectedCandidateId(candidateId);
    candidateStateVersionRef.current += 1;
    const nextCandidate = reviewCandidates.find((candidate) => candidate.id === candidateId) || null;
    const nextShortlistedIds = Array.from(new Set([...finalShortlistedIds, candidateId]));
    const updatedCandidates = reviewCandidates.map((candidate) =>
      candidate.id === candidateId ? { ...candidate, status: "selected" as const } : candidate
    );
    const shortlistedCandidate = nextCandidate ? { ...nextCandidate, status: "selected" as const } : null;
    setReviewCandidates((prev) =>
      prev.map((candidate) => (candidate.id === candidateId ? { ...candidate, status: "selected" } : candidate))
    );
    setRemainingCandidates((prev) => prev.filter((candidate) => candidate.id !== candidateId));
    setShortlistedCandidates((prev) =>
      shortlistedCandidate ? [...prev.filter((candidate) => candidate.id !== candidateId), shortlistedCandidate] : prev
    );
    storeReviewCandidates(jobId, updatedCandidates);
    setFinalShortlistedIds(nextShortlistedIds);
    storeShortlistedCandidateIds(jobId, nextShortlistedIds);

    const result = await selectCandidateForEnrichment({ jobId, candidateId });
    if (!result.success || !result.data) {
      const revertedCandidates = reviewCandidates.map((candidate) =>
        candidate.id === candidateId ? { ...candidate, status: nextCandidate?.status || "new" } : candidate
      );
      setReviewCandidates((prev) =>
        prev.map((candidate) => (candidate.id === candidateId ? { ...candidate, status: nextCandidate?.status || "new" } : candidate))
      );
      setRemainingCandidates((prev) => {
        if (prev.some((candidate) => candidate.id === candidateId)) return prev;
        return nextCandidate ? [nextCandidate, ...prev] : prev;
      });
      setShortlistedCandidates((prev) => prev.filter((candidate) => candidate.id !== candidateId));
      storeReviewCandidates(jobId, revertedCandidates);
      const revertedShortlistedIds = nextShortlistedIds.filter((id) => id !== candidateId);
      setFinalShortlistedIds(revertedShortlistedIds);
      storeShortlistedCandidateIds(jobId, revertedShortlistedIds);
      setError(result.error || "Could not start candidate enrichment.");
      setIsAdvancing(false);
      setSelectedCandidateId("");
      return;
    }

    setActiveCandidate(null);
    setFeedbackMessage("Selection saved. Candidate enrichment is now running.");
    setIsAdvancing(false);
    setSelectedCandidateId("");
  };

  const handleReject = async (candidateId: string) => {
    if (!jobId || isAdvancing) return;
    setIsAdvancing(true);
    setSelectedCandidateId(candidateId);
    candidateStateVersionRef.current += 1;
    const nextShortlistedIds = finalShortlistedIds.filter((id) => id !== candidateId);
    const updatedCandidates = reviewCandidates.map((candidate) =>
      candidate.id === candidateId ? { ...candidate, status: "rejected" as const } : candidate
    );
    setReviewCandidates((prev) =>
      prev.map((candidate) => (candidate.id === candidateId ? { ...candidate, status: "rejected" } : candidate))
    );
    setRemainingCandidates((prev) => prev.filter((candidate) => candidate.id !== candidateId));
    setShortlistedCandidates((prev) => prev.filter((candidate) => candidate.id !== candidateId));
    storeReviewCandidates(jobId, updatedCandidates);
    setFinalShortlistedIds(nextShortlistedIds);
    storeShortlistedCandidateIds(jobId, nextShortlistedIds);
    await swipeCandidate({ jobId, candidateId, action: "reject" });
    setIsAdvancing(false);
    setSelectedCandidateId("");
  };

  const handleContinueToReady = async () => {
    if (!jobId || shortlistedCount === 0 || isContinuingToReady) return;
    setIsContinuingToReady(true);
    setError("");

    const shortlistedCandidates = reviewCandidates.filter((candidate) => completedShortlistedIds.includes(candidate.id));
    storeShortlistedCandidateIds(jobId, completedShortlistedIds);
    storeShortlistedCandidates(jobId, shortlistedCandidates);
    router.push("/ready");

    setIsContinuingToReady(false);
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
    sourcingRefreshCycleRef.current += 1;
    sourcingLoadKeyRef.current = "";
    await loadSourcedCandidates({ source: "manual_refresh", forceRefresh: true });
    setActiveCandidate(null);
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
              Voice intake completed. Candidate profiles and X-Ray sourcing are now ready.
            </div>
          )}

          {(feedbackMessage || calibrationError || sourcingError || error) && (
            <div className="space-y-3">
              {feedbackMessage && <p className="rounded-xl border border-[#DDF5E6] bg-[#F4FBF7] px-4 py-3 text-sm text-[#0F6B3A]">{feedbackMessage}</p>}
              {calibrationError && <p className="rounded-xl border border-amber-100 bg-amber-50 px-4 py-3 text-sm text-amber-800">{calibrationError}</p>}
              {sourcingError && (
                <p className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {sourcingError.includes("409") || sourcingError.toLowerCase().includes("conflict")
                    ? "Sourcing orchestration conflict. The recruiter state should be refreshed before trying again."
                    : sourcingError.toLowerCase().includes("no candidates")
                      ? "X-Ray sourcing completed, but no external candidates were returned."
                      : sourcingError}
                </p>
              )}
              {error && <p className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
            </div>
          )}

          {isLoading && <p className="text-sm text-gray-500">Loading candidate profiles and sourcing candidates...</p>}

          {!isLoading && calibration && !calibrationComplete && calibrationArchetypes.length > 0 && (
            <div className="space-y-8 pt-4 md:pt-6">
              <div className="flex flex-col items-start justify-between gap-5 md:flex-row md:items-center">
                <div className="space-y-2 md:pr-4">
                  <p className="font-body text-[11px] font-semibold uppercase tracking-[0.24em] text-[#0F6B3A]">
                    Ideal candidate profile set {calibrationRoundLabel}
                  </p>
                  <p className="max-w-3xl font-body text-sm leading-6 text-[#6B7280]">
                    Pick the closer resume profile. Each set has 2 profiles, and Adam uses your choice to guide X-Ray sourcing.
                  </p>
                </div>
                <Badge className="inline-flex whitespace-nowrap rounded-full bg-[#EAF4FF] px-5 py-2 text-[13px] font-semibold text-[#1D4ED8] shadow-none">
                  2 profiles
                </Badge>
              </div>

              <div className="grid gap-6 md:grid-cols-2">
                {calibrationArchetypes.map((archetype) => {
                  const profileId = String(archetype.id || archetype.archetype_id || "").trim();
                  const profileData = (archetype.profileData && typeof archetype.profileData === "object" ? archetype.profileData : {}) as Record<string, unknown>;
                  const title = calibrationText(
                    profileData.profileTitle ||
                      profileData.profile_title ||
                    profileData.candidateHeadline ||
                      profileData.candidate_headline ||
                      archetype.title ||
                      archetype.name ||
                      archetype.role ||
                      "Ideal candidate profile"
                  );
                  const locationLabel = calibrationText(profileData.location || profileData.currentLocation || profileData.current_location || archetype.location);
                  const experienceRange = calibrationText(profileData.experienceRange || profileData.experience_range || (archetype.yearsExperience ? `${archetype.yearsExperience} years` : ""));
                  const coreSkills = calibrationValueList(
                    profileData.coreSkills ||
                      profileData.core_skills ||
                      profileData.strongestSkills ||
                      profileData.strongest_skills ||
                      profileData.technicalStrengths ||
                      profileData.technical_strengths ||
                      archetype.skills
                  );
                  const certifications = calibrationValueList(profileData.certifications || profileData.certification || archetype.certifications);
                  const typicalBackground = calibrationText(profileData.typicalBackground || profileData.typical_background || archetype.summary || archetype.headline);
                  const preferredProjectType = calibrationText(profileData.preferredProjectType || profileData.preferred_project_type);
                  const optionalTools = calibrationValueList(profileData.optionalToolsFrameworks || profileData.optional_tools_frameworks);
                  const isChoosing = calibrationSelectionId === profileId;
                  const companyLabel = calibrationText(profileData.typicalCompanies || profileData.typical_companies || profileData.currentCompany || profileData.current_company || archetype.company);
                  const profileSummary = typicalBackground || "Believable resume pattern grounded in the job details.";

                  return (
                    <Card key={profileId || title} className="rounded-[24px] border border-[#E7E0D4] bg-white shadow-[0_8px_24px_rgba(0,0,0,0.04)]">
                      <CardHeader className="space-y-3">
                        <div className="flex items-start justify-between gap-4">
                          <div className="space-y-2">
                            <CardTitle className="font-heading text-[24px] font-semibold text-[#111827]">{title}</CardTitle>
                            <p className="font-body text-[13px] text-[#6B7280]">
                              {[experienceRange, companyLabel, locationLabel].filter(Boolean).join(" Â· ")}
                            </p>
                            <CardDescription className="font-body text-sm leading-6 text-[#6B7280]">
                              {profileSummary}
                            </CardDescription>
                          </div>
                        </div>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        <div className="grid gap-3 rounded-[18px] border border-[#DDF5E6] bg-[#F4FBF7] p-4 text-sm text-[#4B5563]">
                          <div>
                            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0F6B3A]">Core skills</p>
                            <div className="mt-2 flex flex-wrap gap-2">
                              {(coreSkills.length > 0 ? coreSkills : ["Skills from intake"]).slice(0, 6).map((skill) => (
                                <span key={`${profileId}-skill-${skill}`} className="rounded-full bg-white px-3 py-1 text-[12px] font-semibold text-[#0F6B3A] shadow-sm">
                                  {skill}
                                </span>
                              ))}
                            </div>
                          </div>
                          <div className="grid gap-3 md:grid-cols-2">
                            <div className="rounded-2xl bg-white p-3">
                              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#6B7280]">Typical background</p>
                              <p className="mt-1 leading-6 text-[#374151]">{typicalBackground || "Grounded resume pattern tied to the intake."}</p>
                            </div>
                            <div className="rounded-2xl bg-white p-3">
                              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#6B7280]">Preferred project type</p>
                              <p className="mt-1 leading-6 text-[#374151]">{preferredProjectType || "CRUD apps, dashboards, API integrations."}</p>
                            </div>
                          </div>
                          <div className="grid gap-3 md:grid-cols-2">
                            <div className="rounded-2xl bg-white p-3">
                              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#6B7280]">Certifications</p>
                              <p className="mt-1 leading-6 text-[#374151]">{certifications.length > 0 ? certifications.join(", ") : "None required"}</p>
                            </div>
                            <div className="rounded-2xl bg-white p-3">
                              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#6B7280]">Optional tools/frameworks</p>
                              <p className="mt-1 leading-6 text-[#374151]">{optionalTools.length > 0 ? optionalTools.join(", ") : "Same stack as intake"}</p>
                            </div>
                          </div>
                        </div>
                        <Button
                          data-testid={`archetype-select-${profileId}`}
                          className="h-12 w-full rounded-[14px] bg-[#0F6B3A] text-[15px] font-semibold text-white shadow-[0_8px_18px_rgba(15,107,58,0.18)] transition-colors duration-200 hover:bg-[#0C5A31]"
                          onClick={() => void handleCalibrationSelect(profileId)}
                          disabled={isCalibrationLoading || !profileId}
                        >
                          {isChoosing ? "Saving selection..." : "Select profile"}
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
              Ideal candidate profiles are being prepared. Adam will show the selection cards here shortly.
            </div>
          )}

          {calibrationError && <p className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{calibrationError}</p>}

          {!isLoading && calibrationComplete && reviewLoading && (
            <div className="rounded-[20px] border border-[#E7E0D4] bg-white px-4 py-3 text-sm text-[#6B7280]">
              X-Ray sourcing is running now. Candidate cards will appear as soon as the backend returns ranked results.
            </div>
          )}

          {!isLoading && calibrationComplete && swipeCandidates.length > 0 && (
            <div className="space-y-8 pt-4 md:pt-6">
              <div className="flex flex-col items-start justify-between gap-5 md:flex-row md:items-center">
                <div className="space-y-2 md:pr-4">
                  <p className="font-body text-[11px] font-semibold uppercase tracking-[0.24em] text-[#0F6B3A]">
                    X-Ray candidate review
                  </p>
                  <p className="max-w-3xl font-body text-sm leading-6 text-[#6B7280]">
                    Review the externally sourced candidates. Each card is ranked from X-Ray signals and reranked with recruiter memory.
                  </p>
                </div>
              </div>

              <RecruiterSwipeDeck
                candidates={swipeCandidates}
                shortlistedIds={completedShortlistedIds}
                shortlistedCandidates={visibleShortlistedCandidates}
                isAdvancing={isAdvancing}
                selectedCandidateId={selectedCandidateId}
                onOpenDetails={(candidate) => setActiveCandidate(candidate)}
                onSelect={(candidateId) => void handleSelect(candidateId)}
                onReject={(candidateId) => void handleReject(candidateId)}
                onContinueToReady={() => void handleContinueToReady()}
              />

              <div className="rounded-[20px] border border-[#E7E0D4] bg-white px-4 py-4">
                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div>
                    <p className="font-body text-[15px] font-semibold text-[#111827]">Ready is the next recruiter handoff</p>
                    <p className="font-body text-sm text-[#6B7280]">Save selected candidates and continue into Ready when you are satisfied with the shortlist. Ready then hands off into Results.</p>
                  </div>
                  <Button
                    data-testid="continue-to-ready"
                    className="rounded-[14px] bg-[#0F6B3A] px-5 py-2 text-[15px] font-semibold text-white hover:bg-[#0C5A31]"
                    onClick={() => void handleContinueToReady()}
                    disabled={shortlistedCount === 0 || isAdvancing || isContinuingToReady}
                  >
                    {isContinuingToReady ? "Opening Ready..." : "Move to Ready"}
                  </Button>
                </div>
              </div>
            </div>
          )}

          {!isLoading && calibrationComplete && swipeCandidates.length === 0 && !reviewLoading && (
            <div className="space-y-4 rounded-[20px] border border-[#E7E0D4] bg-white px-4 py-4 text-sm text-[#6B7280]">
              <p>
                {sourcingError
                  ? "X-Ray sourcing did not return candidates. Check the sourcing error above."
                  : "X-Ray sourcing finished, but no candidates were returned yet."}
              </p>
              <div className="flex flex-wrap gap-3">
                <Button variant="outline" onClick={() => void refreshFinalResults()}>
                  Retry X-Ray sourcing
                </Button>
                <Button variant="outline" onClick={() => router.push("/voice")}>
                  Back to Voice
                </Button>
              </div>
            </div>
          )}

          <Modal
            open={false}
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
                      <p>Source: {activeCandidate.sourceProvider === "xray_apollo" ? "LinkedIn x-ray" : activeCandidate.sourceProvider || activeCandidate.ats_status_source || "system"}</p>
                      {activeCandidate.sourceQuery ? <p>Query: {activeCandidate.sourceQuery}</p> : null}
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
                        <p>Workflow token: {String(activeInterviewInsights?.workflowToken || activeCandidate.id).slice(0, 12)}â€¦</p>
                        <p>Evaluations: {String(activeInterviewInsights?.evaluationCount ?? 0)}</p>
                        <p>
                          Contact: {activeCandidate.contactEmail || "pending"}
                          {activeCandidate.contactPhone ? ` Â· ${activeCandidate.contactPhone}` : ""}
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
                    disabled={isAdvancing || selectedCandidateId !== "" || finalShortlistedIds.includes(activeCandidate.id) || isShortlistedStatus(activeCandidate.status) || isShortlistedStatus(activeCandidate.ats_status)}
                  >
                    {isAdvancing && selectedCandidateId === activeCandidate.id
                      ? "Starting enrichment..."
                      : finalShortlistedIds.includes(activeCandidate.id) || isShortlistedStatus(activeCandidate.status) || isShortlistedStatus(activeCandidate.ats_status)
                        ? "Shortlisted"
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
          <RecruiterCandidateModal
            candidate={activeCandidate}
            open={Boolean(activeCandidate)}
            onClose={() => setActiveCandidate(null)}
            onReject={() => {
              if (activeCandidate) void handleReject(activeCandidate.id);
            }}
            onShortlist={() => {
              if (activeCandidate) void handleSelect(activeCandidate.id);
            }}
            isAdvancing={isAdvancing}
            selectedCandidateId={selectedCandidateId}
          />
          <div className="mt-6 flex items-center justify-center gap-2 font-body text-sm text-[#6B7280]">
            <ShieldCheck className="h-4 w-4 text-[#0F6B3A]" />
            <span>Your selection helps us improve future matches</span>
          </div>
        </div>
      </div>
    </AppShell>
  );
}




