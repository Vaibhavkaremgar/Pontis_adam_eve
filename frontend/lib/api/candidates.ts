/**
 * What this file does:
 * Retrieves ranked candidates for a job.
 *
 * What API it connects to:
 * GET /candidates?jobId=...&refined=true|false
 *
 * How it fits in the pipeline:
 * Frontend displays returned candidates; retrieval/ranking logic stays in backend.
 */
import { API_BASE_URL } from "@/lib/config";
import type { Candidate } from "@/types";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";

type CandidateQuery = {
  jobId: string;
  refined?: boolean;
};

/** This function calls backend API and returns structured response. */
export async function getCandidates({ jobId, refined }: CandidateQuery): Promise<ApiResponse<Candidate[]>> {
  const params = new URLSearchParams({ jobId });
  if (refined) params.set("refined", "true");

  return requestApi<Candidate[]>({
    url: `${API_BASE_URL}/candidates?${params.toString()}`,
    method: "GET"
  });
}
