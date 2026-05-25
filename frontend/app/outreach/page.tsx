"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAppContext } from "@/context/AppContext";

export default function OutreachPage() {
  const router = useRouter();
  const { user, isSessionReady, jobId, isRefined } = useAppContext();

  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    if (!jobId) {
      router.replace("/job");
      return;
    }
    if (!isRefined) {
      router.replace(`/voice?jobId=${encodeURIComponent(jobId)}`);
    }
  }, [isRefined, isSessionReady, jobId, router, user]);

  return (
    <AppShell activeStep={5}>
      <Card className="mx-auto w-full max-w-[640px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Automation handoff</CardTitle>
          <CardDescription>
            Handoff is selection-driven now. When the recruiter selects a candidate, Adam handles enrichment,
            messaging, and ATS updates in the background.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-5">
          <div className="rounded-xl border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] p-4 text-sm text-gray-700">
            There is no separate manual messaging workflow here. Use Review to select candidates, then check Ready for
            passive status updates.
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Button className="w-full justify-center" onClick={() => router.push("/review")}>
              Back to Review
            </Button>
            <Button variant="outline" className="w-full justify-center" onClick={() => router.push("/ready")}>
              Go to Ready
            </Button>
          </div>
        </CardContent>
      </Card>
    </AppShell>
  );
}
