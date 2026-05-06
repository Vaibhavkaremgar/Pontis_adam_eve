/**
 * What this file does:
 * Returns candidate search results in standardized envelope.
 *
 * What API it connects to:
 * GET /api/candidates?jobId=...&refined=true|false&mode=volume|elite
 *
 * How it fits in the pipeline:
 * Mock retrieval/ranking output for frontend rendering and flow validation.
 * Current /app/api routes are mock implementations.
 * These will be replaced by real backend APIs later (FastAPI server).
 */
import { NextResponse } from "next/server";

import type { Candidate } from "@/types";

const roles = [
  "Senior Product Designer",
  "Staff Frontend Engineer",
  "Talent Intelligence Specialist",
  "Recruiting Operations Manager",
  "People Analytics Partner",
  "Principal Backend Engineer",
  "Engineering Manager",
  "Technical Recruiter",
  "Applied ML Engineer",
  "Solutions Architect"
];

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const jobId = searchParams.get("jobId");
  const refined = searchParams.get("refined") === "true";
  const mode = searchParams.get("mode") === "elite" ? "elite" : "volume";

  if (!jobId) {
    return NextResponse.json(
      {
        success: false,
        data: null,
        error: "jobId is required"
      },
      { status: 400 }
    );
  }

  const candidateCount = mode === "elite" ? 5 : 10;

  const candidates: Candidate[] = Array.from({ length: candidateCount }).map((_, index) => {
    const baseScore = mode === "elite" ? 4.95 - index * 0.18 : 4.8 - index * 0.3;
    const fitScore = refined ? Math.min(5, Number((baseScore + (mode === "elite" ? 0.05 : 0.1)).toFixed(2))) : Number(baseScore.toFixed(2));
    const strategy: Candidate["strategy"] = fitScore >= 4 ? "HIGH" : fitScore >= 2.5 ? "MEDIUM" : "LOW";
    const skills = mode === "elite"
      ? ["communication", "execution", "stakeholder management", "systems thinking"]
      : ["communication", "execution"];

    return {
      id: `${jobId}_cand_${index + 1}`,
      name: `Candidate ${index + 1}`,
      role: roles[index],
      company: "Demo Company",
      skills,
      summary: mode === "elite"
        ? "LLM-ranked shortlist profile with strong evidence for role fit, seniority alignment, and execution depth."
        : "",
      fitScore,
      decision: fitScore >= 3.8 ? "strong_match" : fitScore >= 2.5 ? "potential" : "weak",
      explanation: mode === "elite"
        ? {
            semanticScore: Number((fitScore / 5).toFixed(3)),
            skillOverlap: 0.78,
            finalScore: Number((fitScore / 5).toFixed(3)),
            pdlRelevance: Number((fitScore / 5).toFixed(3)),
            recencyScore: 0.72,
            skillsMatched: skills.slice(0, 3),
            experienceMatch: "Strong match to 5-7 years expected seniority",
            candidateExperience: "6+ years",
            jobExperience: "5-7 years",
            aiReasoning:
              "High-confidence shortlist recommendation: the profile shows direct role alignment, clear evidence of impact, and enough seniority to handle the scope with limited ramp-up.",
            penalties: {
              semanticPenalty: 0.12,
              missingSkillsPenalty: 0
            }
          }
        : undefined,
      strategy,
      status: index % 4 === 0 ? "interview_invited" : index % 3 === 0 ? "interview_scheduled" : index % 2 === 0 ? "contacted" : "new"
    };
  });

  return NextResponse.json(
    {
      success: true,
      data: candidates
    },
    { status: 200 }
  );
}
