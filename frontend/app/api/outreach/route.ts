/**
 * What this file does:
 * Accepts selected candidates and returns outreach confirmation in standardized envelope.
 *
 * What API it connects to:
 * POST /api/outreach
 *
 * How it fits in the pipeline:
 * Mock outreach handoff before real Slack/notification integration.
 * Current /app/api routes are mock implementations.
 * These will be replaced by real backend APIs later (FastAPI server).
 */
import { NextResponse } from "next/server";

export async function POST(request: Request) {
  const body = (await request.json()) as { jobId?: string; selectedCandidates?: string[] };

  if (!body.jobId || !Array.isArray(body.selectedCandidates) || body.selectedCandidates.length === 0) {
    return NextResponse.json(
      {
        success: false,
        data: null,
        error: "jobId and selectedCandidates are required"
      },
      { status: 400 }
    );
  }

  return NextResponse.json(
    {
      success: true,
      data: {
        message: "Candidates sent for outreach"
      }
    },
    { status: 200 }
  );
}
