/**
 * What this file does:
 * Defines the shared API response envelope used across frontend API clients.
 *
 * What API it connects to:
 * Applies to every backend endpoint consumed by /lib/api.
 *
 * How it fits in the pipeline:
 * Standardizes success/error handling so pages can manage loading/error states consistently.
 */

export type ApiResponse<T> = {
  success: boolean;
  data: T | null;
  error: string | null;
};
