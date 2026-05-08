/**
 * What this file does:
 * Handles recruiter authentication via OTP email flow and Google OAuth.
 *
 * What API it connects to:
 * POST /auth/request-otp  - sends OTP to email
 * POST /auth/verify-otp   - verifies OTP and sets auth cookies
 * POST /auth/google       - Google OAuth token exchange
 * POST /auth/logout       - clears auth cookies
 * GET  /auth/me           - restores the current user from cookies
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
type LoginData = { user: User; token?: string; access_token?: string };
type GoogleLoginPayload = { token: string };
type CurrentUserData = { user: User };

export async function requestOtp(payload: OtpRequestPayload): Promise<ApiResponse<OtpRequestData>> {
  return requestApi<OtpRequestData>({
    url: `${API_BASE_URL}/auth/request-otp`,
    method: "POST",
    payload
  });
}

export async function verifyOtp(payload: OtpVerifyPayload): Promise<ApiResponse<LoginData>> {
  return requestApi<LoginData>({
    url: `${API_BASE_URL}/auth/verify-otp`,
    method: "POST",
    payload
  });
}

export async function loginWithGoogle(payload: GoogleLoginPayload): Promise<ApiResponse<LoginData>> {
  return requestApi<LoginData>({
    url: `${API_BASE_URL}/auth/google`,
    method: "POST",
    payload
  });
}

export async function getCurrentUser(): Promise<ApiResponse<CurrentUserData>> {
  return requestApi<CurrentUserData>({
    url: `${API_BASE_URL}/auth/me`,
    method: "GET"
  });
}

export async function logout(): Promise<ApiResponse<{ loggedOut: boolean }>> {
  return requestApi<{ loggedOut: boolean }>({
    url: `${API_BASE_URL}/auth/logout`,
    method: "POST"
  });
}
