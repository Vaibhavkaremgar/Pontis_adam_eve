"use client";

import { useRouter } from "next/navigation";
import { BriefcaseBusiness, ChartNoAxesCombined, ChevronRight, ShieldCheck } from "lucide-react";

import { AppShell } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAppContext } from "@/context/AppContext";

export default function WorkspacePage() {
  const router = useRouter();
  const { user } = useAppContext();

  return (
    <AppShell activeStep={1}>
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-2 py-6">
        <div className="rounded-[28px] border border-[rgba(120,100,80,0.08)] bg-[linear-gradient(135deg,#F8F5EE_0%,#EFE6D8_55%,#F6F1E8_100%)] p-8 shadow-[0_18px_50px_rgba(0,0,0,0.04)]">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="space-y-3">
              <div className="inline-flex items-center gap-2 rounded-full border border-[rgba(120,100,80,0.1)] bg-white/70 px-3 py-1 text-xs font-medium text-gray-700">
                <ShieldCheck className="h-4 w-4 text-emerald-600" />
                Signed in as {user?.email || "client"}
              </div>
              <div>
                <h1 className="text-3xl font-semibold tracking-tight text-gray-900">What would you like to do next?</h1>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-gray-600">
                  Use the same login to either start a new hiring flow or jump into the results workspace for completed interviews.
                </p>
              </div>
            </div>
          </div>
        </div>

        <div className="grid gap-5 md:grid-cols-2">
          <Card className="border-[rgba(120,100,80,0.08)] bg-white shadow-[0_10px_30px_rgba(0,0,0,0.03)]">
            <CardHeader>
              <div className="mb-3 inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-[#F3EDE3] text-[#0F6B3A]">
                <BriefcaseBusiness className="h-5 w-5" />
              </div>
              <CardTitle>Hire a candidate</CardTitle>
              <CardDescription>Start a new hiring flow with company details, job intake, voice intake, and review.</CardDescription>
            </CardHeader>
            <CardContent>
              <Button className="w-full justify-between" onClick={() => router.push("/company")}>
                Start hiring
                <ChevronRight className="h-4 w-4" />
              </Button>
            </CardContent>
          </Card>

          <Card className="border-[rgba(120,100,80,0.08)] bg-white shadow-[0_10px_30px_rgba(0,0,0,0.03)]">
            <CardHeader>
              <div className="mb-3 inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-[#EEF7F0] text-[#0F6B3A]">
                <ChartNoAxesCombined className="h-5 w-5" />
              </div>
              <CardTitle>Go to results</CardTitle>
              <CardDescription>Open the client results section to view candidates, recordings, transcripts, and AI analysis.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Button className="w-full justify-between" variant="outline" onClick={() => router.push("/results")}>
                Open results
                <ChevronRight className="h-4 w-4" />
              </Button>
              <p className="text-xs leading-5 text-gray-500">
                Results will stay scoped to the logged-in company once the company-id filter is applied end to end.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}
