/**
 * What this file does:
 * Renders top navigation for all steps.
 *
 * What API it connects to:
 * No direct API calls here.
 *
 * How it fits in the pipeline:
 * Gives consistent orientation while recruiter moves through backend-connected workflow stages.
 */
import Link from "next/link";

export function Navbar() {
  return (
    <header className="border-b border-[rgba(120,100,80,0.08)] bg-[#EFE6D8]">
      <div className="mx-auto flex w-full max-w-2xl items-center justify-between px-4 py-3">
        <Link href="/company" className="text-lg font-semibold text-gray-900">
          Pontis
        </Link>
        <p className="text-xs text-gray-600">Hiring Intake Flow</p>
      </div>
    </header>
  );
}
