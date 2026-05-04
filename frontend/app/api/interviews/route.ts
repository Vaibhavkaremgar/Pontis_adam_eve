/**
 * What this file does:
 * Returns interview-ready candidate statuses in standardized envelope.
 *
 * What API it connects to:
 * GET /api/interviews?jobId=...
 *
 * How it fits in the pipeline:
 * Mock final-stage interview readiness output.
 * Current /app/api routes are mock implementations.
 * These will be replaced by real backend APIs later (FastAPI server).
 */
import { NextResponse } from "next/server";

import type { InterviewStatus } from "@/types";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const jobId = searchParams.get("jobId");

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

  const items: InterviewStatus[] = [
    {
      candidateId: `${jobId}_cand_1`,
      name: "Candidate One",
      status: "shortlisted"
    },
    {
      candidateId: `${jobId}_cand_2`,
      name: "Candidate Two",
      status: "contacted"
    },
    {
      candidateId: `${jobId}_cand_3`,
      name: "Candidate Three",
      status: "interview_scheduled"
    },
    {
      candidateId: `${jobId}_cand_4`,
      name: "Candidate Four",
      status: "interview_invited"
    }
  ];

  return NextResponse.json(
    {
      success: true,
      data: items
    },
    { status: 200 }
  );
}
