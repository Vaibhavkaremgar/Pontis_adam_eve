/**
 * What this file does:
 * Creates a hiring session and returns jobId in standardized envelope.
 *
 * What API it connects to:
 * POST /api/hiring/create
 *
 * How it fits in the pipeline:
 * Simulates backend entry point for embedding/vector pipeline handoff.
 * Current /app/api routes are mock implementations.
 * These will be replaced by real backend APIs later (FastAPI server).
 */
import { NextResponse } from "next/server";

export async function POST(request: Request) {
  const body = (await request.json()) as {
    company?: { name?: string; website?: string };
    job?: { title?: string; description?: string };
  };

  if (!body.company?.name || !body.company.website || !body.job?.title || !body.job.description) {
    return NextResponse.json(
      {
        success: false,
        data: null,
        error: "Missing required company/job fields"
      },
      { status: 400 }
    );
  }

  const jobId = `job_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

  return NextResponse.json(
    {
      success: true,
      data: { jobId }
    },
    { status: 200 }
  );
}
