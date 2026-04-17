/**
 * What this file does:
 * Creates hiring job records from recruiter company/job input.
 *
 * What API it connects to:
 * POST /hiring/create
 *
 * How it fits in the pipeline:
 * Backend converts this input into embeddings/vector entries; frontend only sends structured input and stores jobId.
 */
import { API_BASE_URL } from "@/lib/config";
import type { Company, Job } from "@/types";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

type HiringCreatePayload = {
  company: Company;
  job: Job;
};

type HiringCreateData = {
  jobId: string;
};

/** This function calls backend API and returns structured response. */
export async function createHiring(payload: HiringCreatePayload): Promise<ApiResponse<HiringCreateData>> {
  return requestApi<HiringCreateData>({
    url: `${API_BASE_URL}/hiring/create`,
    method: "POST",
    payload
  });
}
