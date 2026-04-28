/**
 * What this file does:
 * Handles recruiter authentication via OTP email flow and Google OAuth.
 *
 * What API it connects to:
 * POST /auth/request-otp  — sends OTP to email
 * POST /auth/verify-otp   — verifies OTP and returns JWT
 * POST /auth/google        — Google OAuth token exchange
 *
 * How it fits in the pipeline:
 * Entry gate before recruiter can access company/job/candidate pipeline.
 */
import { API_BASE_URL } from "@/lib/config";
import type { User } from "@/types";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

type OtpRequestPayload = { email: string };
type OtpRequestData = { message: string; email: string };

type OtpVerifyPayload = { email: string; otp: string };
type LoginData = { user: User; token: string; access_token?: string };

type GoogleLoginPayload = { token: string };

export async function requestOtp(payload: OtpRequestPayload): Promise<ApiResponse<OtpRequestData>> {
  return requestApi<OtpRequestData>({
    url: `${API_BASE_URL}/auth/request-otp`,
    method: "POST",
    payload,
  });
}

export async function verifyOtp(payload: OtpVerifyPayload): Promise<ApiResponse<LoginData>> {
  const response = await requestApi<LoginData>({
    url: `${API_BASE_URL}/auth/verify-otp`,
    method: "POST",
    payload,
  });

  if (response.success && response.data?.user && !response.data.user.provider) {
    response.data.user.provider = "email";
  }

  return response;
}

export async function loginWithGoogle(payload: GoogleLoginPayload): Promise<ApiResponse<LoginData>> {
  const response = await requestApi<LoginData>({
    url: `${API_BASE_URL}/auth/google`,
    method: "POST",
    payload,
  });

  if (response.success && response.data?.user && !response.data.user.provider) {
    response.data.user.provider = "google";
  }

  return response;
}
