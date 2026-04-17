/**
 * What this file does:
 * Implements recruiter login endpoint with standardized response envelope.
 *
 * What API it connects to:
 * POST /api/auth/login
 *
 * How it fits in the pipeline:
 * Returns authenticated user profile and token for frontend session orchestration.
 * Current /app/api routes are mock implementations.
 * These will be replaced by real backend APIs later (FastAPI server).
 */
import { NextResponse } from "next/server";

export async function POST(request: Request) {
  const body = (await request.json()) as { email?: string; provider?: "email" | "google" };

  if (!body.email || !body.email.trim()) {
    return NextResponse.json(
      {
        success: false,
        data: null,
        error: "Email is required"
      },
      { status: 400 }
    );
  }

  const user = {
    id: `user_${Math.random().toString(36).slice(2, 10)}`,
    email: body.email.trim().toLowerCase(),
    provider: body.provider ?? "email",
    name: body.email.split("@")[0]
  };

  const token = `mock_token_${Math.random().toString(36).slice(2, 14)}`;

  return NextResponse.json(
    {
      success: true,
      data: { user, token }
    },
    { status: 200 }
  );
}
