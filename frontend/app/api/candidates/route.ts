/**
 * What this file does:
 * Returns candidate search results in standardized envelope.
 *
 * What API it connects to:
 * GET /api/candidates?jobId=...&refined=true|false
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

  const candidates: Candidate[] = Array.from({ length: 10 }).map((_, index) => {
    const baseScore = 4.8 - index * 0.3;
    const fitScore = refined ? Math.min(5, Number((baseScore + 0.1).toFixed(2))) : Number(baseScore.toFixed(2));
    const strategy: Candidate["strategy"] = fitScore >= 4 ? "HIGH" : fitScore >= 2.5 ? "MEDIUM" : "LOW";

    return {
      id: `${jobId}_cand_${index + 1}`,
      name: `Candidate ${index + 1}`,
      role: roles[index],
      company: "Demo Company",
      skills: ["communication", "execution"],
      summary: refined
        ? "Refined profile based on recruiter voice calibration and role-specific constraints."
        : "Ranked profile based on job brief relevance and sourcing match signals.",
      fitScore,
      decision: fitScore >= 3.8 ? "strong_match" : fitScore >= 2.5 ? "potential" : "weak",
      explanation: {
        semanticScore: Number((fitScore / 5).toFixed(3)),
        skillOverlap: 0.4,
        finalScore: Number((fitScore / 5).toFixed(3)),
        pdlRelevance: Number((fitScore / 5).toFixed(3)),
        recencyScore: 0.5,
        skillsMatched: ["communication", "execution"],
        experienceMatch: "4 years vs 3-5 years",
        candidateExperience: "4 years",
        jobExperience: "3-5 years",
        penalties: {
          semanticPenalty: 1,
          missingSkillsPenalty: 1
        }
      },
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
