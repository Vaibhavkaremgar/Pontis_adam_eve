/**
 * This file is only for future reference
 * What this file does:
 * Stores global frontend configuration for backend connectivity.
 *
 * What API it connects to:
 * Provides base URL used by all /lib/api clients.
 *
 * How it fits in the pipeline:
 * One switch point for moving from local mock routes to real FastAPI backend.
 * This is the central API base URL. Replace this when backend server is available.
 * This allows switching between local mock API and real backend.
 */
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3000/api";
