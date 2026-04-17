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

const names = [
  "Avery Patel",
  "Riley Morgan",
  "Jordan Lee",
  "Samira Khan",
  "Tyler Brooks",
  "Nina Alvarez",
  "Marcus Chen",
  "Leah Okafor",
  "Diego Ramos",
  "Priya Nair"
];

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
    const baseScore = 94 - index * 4;
    const fitScore = refined ? Math.min(99, baseScore + 2) : baseScore;
    const strategy: Candidate["strategy"] = fitScore >= 88 ? "HIGH" : fitScore >= 76 ? "MEDIUM" : "LOW";

    return {
      id: `${jobId}_cand_${index + 1}`,
      name: names[index],
      role: roles[index],
      summary: refined
        ? "Refined profile based on recruiter voice calibration and role-specific constraints."
        : "Ranked profile based on job brief relevance and sourcing match signals.",
      fitScore,
      strategy,
      status: index % 3 === 0 ? "Replied" : index % 2 === 0 ? "Sent" : "New"
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
