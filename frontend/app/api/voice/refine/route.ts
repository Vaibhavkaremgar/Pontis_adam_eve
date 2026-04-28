/**
 * What this file does:
 * Accepts voice notes and confirms refinement trigger using standardized envelope.
 *
 * What API it connects to:
 * POST /api/voice/refine
 *
 * How it fits in the pipeline:
 * Mock endpoint for voice-to-search refinement step.
 * Current /app/api routes are mock implementations.
 * These will be replaced by real backend APIs later (FastAPI server).
 */
import { NextResponse } from "next/server";

export async function POST(request: Request) {
  const body = (await request.json()) as { jobId?: string; voiceNotes?: string[] };

  if (!body.jobId || !Array.isArray(body.voiceNotes) || body.voiceNotes.length === 0) {
    return NextResponse.json(
      {
        success: false,
        data: null,
        error: "jobId and voiceNotes are required"
      },
      { status: 400 }
    );
  }

  return NextResponse.json(
    {
      success: true,
      data: {
        refined: true,
        job: {
          title: "Refined role",
          description: "Refined description",
          location: "",
          compensation: "",
          skills_required: [],
          responsibilities: [],
          experience_level: ""
        },
        extraction: {
          success: true,
          usedFallback: true,
          confidence: 0.5,
          fields: ["role", "skills", "experience"]
        }
      }
    },
    { status: 200 }
  );
}
