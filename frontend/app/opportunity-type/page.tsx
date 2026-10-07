"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { BriefcaseBusiness, GraduationCap } from "lucide-react";

import { AppShell } from "@/components/layout/app-shell";
import { useAppContext } from "@/context/AppContext";

const OPTIONS = [
  {
    value: "jobs" as const,
    label: "Full-Time",
    description: "Hire for a full-time role.",
    icon: BriefcaseBusiness,
  },
  {
    value: "intern" as const,
    label: "Intern",
    description: "Hire for an internship opportunity.",
    icon: GraduationCap,
  },
];

export default function OpportunityTypePage() {
  const router = useRouter();
  const { user, isSessionReady, job, setJob } = useAppContext();

  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) router.replace("/login");
  }, [isSessionReady, router, user]);

  const selectOpportunityType = (opportunityType: "jobs" | "intern") => {
    setJob({ ...job, opportunityType });
    router.push("/company");
  };

  return (
    <AppShell activeStep={1}>
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-8 px-4 py-16">
        <div className="text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-[#0F6B3A]">Opportunity Type</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-gray-900">Select Opportunity Type</h1>
          <p className="mt-2 text-sm text-gray-500">Choose one option to continue with the hiring workflow.</p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          {OPTIONS.map(({ value, label, description, icon: Icon }) => (
            <button
              key={value}
              type="button"
              data-testid={`opportunity-type-${value}`}
              onClick={() => selectOpportunityType(value)}
              className="group flex min-h-44 flex-col items-start gap-4 rounded-2xl border border-[rgba(120,100,80,0.12)] bg-white p-6 text-left shadow-sm transition hover:border-[#0F6B3A]/30 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-[#0F6B3A]/30"
            >
              <span className="inline-flex h-12 w-12 items-center justify-center rounded-xl bg-[#F3EDE3] text-[#0F6B3A]">
                <Icon className="h-6 w-6" />
              </span>
              <span>
                <span className="block text-lg font-semibold text-gray-900">{label}</span>
                <span className="mt-1 block text-sm text-gray-500">{description}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
