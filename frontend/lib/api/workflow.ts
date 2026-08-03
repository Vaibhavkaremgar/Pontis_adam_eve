/**
 * What this file does:
 * Resolves secure Eve workflow links back into the recruiter job context.
 *
 * What API it connects to:
 * GET /workflow/{workflowToken}
 *
 * How it fits in the pipeline:
 * Lets the recruiter open a tokenized link without exposing raw job IDs in the URL.
 */
import { API_BASE_URL } from "@/lib/config";

import { requestApi } from "./client";
import type { ApiResponse } from "./types";
import type { Company, Job } from "@/types";

export type EveWorkflowContext = {
  workflowToken: string;
  workflowLink: string;
  jobId: string;
  companyId: string;
  job: Job;
  company: Company;
  workflow: {
    status: string;
    linkedinPosting?: Record<string, unknown>;
  };
};

export async function getEveWorkflowContext(workflowToken: string): Promise<ApiResponse<EveWorkflowContext>> {
  return requestApi<EveWorkflowContext>({
    url: `${API_BASE_URL}/workflow/${encodeURIComponent(workflowToken)}`,
    method: "GET",
  });
}
