/**
 * What this file does:
 * Handles recruiter authentication requests.
 *
 * What API it connects to:
 * POST /auth/login
 *
 * How it fits in the pipeline:
 * Frontend authenticates recruiter identity and receives user + token to unlock the hiring flow.
 */
import { API_BASE_URL } from "@/lib/config";
import type { User } from "@/types";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

type LoginPayload = {
  email: string;
  provider?: "email" | "google";
};

type LoginData = {
  user: User;
  token: string;
};

/** This function calls backend API and returns structured response. */
export async function login(payload: LoginPayload): Promise<ApiResponse<LoginData>> {
  return requestApi<LoginData>({
    url: `${API_BASE_URL}/auth/login`,
    method: "POST",
    payload
  });
}
