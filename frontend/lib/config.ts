/**
 * This file is only for future reference
 * What this file does:
 * Stores global frontend configuration for backend connectivity.
 *
 * What API it connects to:
 * Provides base URL used by all /lib/api clients.
 *
 * How it fits in the pipeline:
 * One switch point for the backend proxy route used by the frontend.
 * The browser calls the local Next.js route, which proxies to Railway backend.
 */
export const API_BASE_URL = "/api/backend";
