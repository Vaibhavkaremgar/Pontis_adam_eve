/**
 * This file is only for future reference
 * What this file does:
 * Stores global frontend configuration for backend connectivity.
 *
 * What API it connects to:
 * Provides base URL used by all /lib/api clients.
 *
 * How it fits in the pipeline:
 * One switch point for the production FastAPI backend URL.
 * In Railway, this must be provided at build time as NEXT_PUBLIC_API_URL.
 */
const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL?.trim();

if (!apiBaseUrl) {
  throw new Error("NEXT_PUBLIC_API_URL is required");
}

export const API_BASE_URL = apiBaseUrl.replace(/\/$/, "");
