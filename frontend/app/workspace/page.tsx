"use client";

import { useRouter } from "next/navigation";
import { BriefcaseBusiness, ChartNoAxesCombined, ChevronRight } from "lucide-react";

import { AppShell } from "@/components/layout/app-shell";
import { useAppContext } from "@/context/AppContext";

export default function WorkspacePage() {
  const router = useRouter();
  const { user } = useAppContext();

  return (
    <AppShell activeStep={1}>
      <div className="mx-auto flex w-full max-w-2xl flex-col items-center gap-10 px-4 py-16">
        <div className="text-center">
          <h1 className="text-3xl font-semibold tracking-tight text-gray-900">
            Welcome
          </h1>
          <p className="mt-2 text-sm text-gray-500">What would you like to do?</p>
        </div>

        <div className="grid w-full gap-4 sm:grid-cols-2">
          <button
            onClick={() => router.push("/company")}
            className="group flex flex-col gap-4 rounded-2xl border border-[rgba(120,100,80,0.1)] bg-white p-6 text-left shadow-sm transition hover:shadow-md"
          >
            <div className="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-[#F3EDE3] text-[#0F6B3A]">
              <BriefcaseBusiness className="h-5 w-5" />
            </div>
            <div>
              <p className="font-semibold text-gray-900">Start a new hire</p>
              <p className="mt-1 text-sm text-gray-500">Set up a job and run the full candidate pipeline.</p>
            </div>
            <ChevronRight className="mt-auto h-4 w-4 text-gray-400 transition group-hover:translate-x-1" />
          </button>

          <button
            onClick={() => router.push("/results")}
            className="group flex flex-col gap-4 rounded-2xl border border-[rgba(120,100,80,0.1)] bg-white p-6 text-left shadow-sm transition hover:shadow-md"
          >
            <div className="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-[#EEF7F0] text-[#0F6B3A]">
              <ChartNoAxesCombined className="h-5 w-5" />
            </div>
            <div>
              <p className="font-semibold text-gray-900">View results</p>
              <p className="mt-1 text-sm text-gray-500">Review candidates, transcripts, and AI analysis.</p>
            </div>
            <ChevronRight className="mt-auto h-4 w-4 text-gray-400 transition group-hover:translate-x-1" />
          </button>
        </div>
      </div>
    </AppShell>
  );
}
