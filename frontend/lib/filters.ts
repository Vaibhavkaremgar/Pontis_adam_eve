/**
 * What this file does:
 * Builds structured search filters from recruiter job input and voice notes.
 *
 * What API it connects to:
 * This output is intended for external candidate search providers.
 *
 * How it fits in the pipeline:
 * Frontend prepares normalized filters, then backend/API clients can use them for candidate retrieval.
 */
import type { Job } from "@/types";

export type CandidateSearchFilters = {
  role: string;
  skills: string[];
  experience: string;
  location: string;
};

const SKILL_KEYWORDS = [
  "react",
  "typescript",
  "javascript",
  "node",
  "python",
  "java",
  "aws",
  "gcp",
  "azure",
  "sql",
  "graphql",
  "next.js",
  "product design",
  "figma",
  "recruiting",
  "sourcing"
];

function findExperienceText(input: string): string {
  const match = input.match(/(\d+\+?\s*(?:years?|yrs?))/i);
  return match ? match[1] : "Not specified";
}

/**
 * This is used to query external candidate APIs like PDL or Apollo.
 */
export function extractCandidateFilters(job: Job, voiceNotes: string[]): CandidateSearchFilters {
  const mergedNotes = voiceNotes.join(" ").toLowerCase();
  const combinedText = `${job.title} ${job.description} ${mergedNotes}`.toLowerCase();

  const skills = SKILL_KEYWORDS.filter((skill) => combinedText.includes(skill));

  const experienceFromJob = findExperienceText(job.description);
  const experienceFromVoice = findExperienceText(mergedNotes);
  const experience = experienceFromVoice !== "Not specified" ? experienceFromVoice : experienceFromJob;

  return {
    role: job.title.trim() || "Unknown Role",
    skills,
    experience,
    location: job.location.trim() || "Any"
  };
}

