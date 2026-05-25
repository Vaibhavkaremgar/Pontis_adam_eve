"use client";

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
import { usePathname } from "next/navigation";
import { ArrowLeft, LogOut } from "lucide-react";

import { useAppContext } from "@/context/AppContext";

const BACK_TARGETS: Array<{ pattern: RegExp; href: string; label: string }> = [
  { pattern: /^\/voice\/processing$/, href: "/voice", label: "Back to Voice Intake" },
  { pattern: /^\/voice$/, href: "/job", label: "Back to Job Details" },
  { pattern: /^\/review$/, href: "/voice", label: "Back to Voice Intake" },
  { pattern: /^\/outreach$/, href: "/review", label: "Back to Review" },
  { pattern: /^\/ready$/, href: "/review", label: "Back to Review" },
  { pattern: /^\/interview\/book$/, href: "/ready", label: "Back to Ready" },
  { pattern: /^\/job$/, href: "/company", label: "Back to Company" },
  { pattern: /^\/company$/, href: "/job", label: "Back to Jobs" },
];

export function Navbar() {
  const pathname = usePathname();
  const { logout } = useAppContext();
  const backTarget = BACK_TARGETS.find((entry) => entry.pattern.test(pathname || "")) || null;

  return (
    <header className="border-b border-[rgba(120,100,80,0.08)] bg-[#EDE5D8]">
      <div className="mx-auto flex w-full max-w-[1600px] items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8">
        <div className="flex min-w-0 items-center gap-3">
          {backTarget ? (
            <Link
              href={backTarget.href}
              className="inline-flex items-center gap-2 rounded-full border border-[rgba(95,74,49,0.12)] bg-[#F4EEE3] px-4 py-2 text-sm font-medium text-[#403325] shadow-[0_2px_8px_rgba(0,0,0,0.04)] transition-all hover:bg-white hover:shadow-[0_6px_16px_rgba(0,0,0,0.06)]"
            >
              <ArrowLeft className="h-4 w-4" />
              <span>{backTarget.label}</span>
            </Link>
          ) : (
            <div className="h-10" />
          )}

          <Link href="/company" className="flex items-center gap-3 text-[#111827]">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-[rgba(95,74,49,0.14)] bg-[#F6F0E6] font-heading text-2xl font-semibold leading-none text-[#111827] shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
              P
            </div>
            <span className="font-heading text-[30px] font-semibold leading-none tracking-[-0.02em] text-[#111827]">
              Pontis
            </span>
          </Link>
        </div>

        <button
          type="button"
          onClick={logout}
          className="inline-flex items-center gap-2 rounded-full px-3 py-2 text-sm font-medium text-[#6B5C4A] transition-colors hover:bg-[#F4EEE3] hover:text-[#3E3428]"
        >
          <LogOut className="h-4 w-4" />
          <span>Sign out</span>
        </button>
      </div>
    </header>
  );
}
