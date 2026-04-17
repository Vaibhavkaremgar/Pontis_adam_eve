/**
 * What this file does:
 * Redirects root URL to the login route.
 *
 * What API it connects to:
 * No direct API calls here.
 *
 * How it fits in the pipeline:
 * Enforces login as the first step before any backend pipeline orchestration begins.
 */
import { redirect } from "next/navigation";

export default function HomePage() {
  redirect("/login");
}
