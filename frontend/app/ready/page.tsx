"use client";

/**
 * What this file does:
 * Displays interview-ready candidates and statuses.
 *
 * What API it connects to:
 * Uses /lib/api/interviews -> GET /interviews?jobId=...
 *
 * How it fits in the pipeline:
 * Final frontend stage that visualizes backend interview-readiness output.
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAppContext } from "@/context/AppContext";
import { exportCandidates } from "@/lib/api/candidates";
import { getInterviewStatuses } from "@/lib/api/interviews";
import type { InterviewStatus } from "@/types";

export default function ReadyPage() {
  const router = useRouter();
  const { user, isSessionReady, jobId } = useAppContext();
  const [items, setItems] = useState<InterviewStatus[]>([]);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const [isExporting, setIsExporting] = useState(false);

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

    const run = async () => {
      // This handles real-world API delays and failures.
      setIsLoading(true);
      setError("");

      const result = await getInterviewStatuses(jobId);
      if (!result.success || !result.data) {
        setError(result.error || "Could not load interview statuses.");
        setIsLoading(false);
        return;
      }

      setItems(result.data);
      setIsLoading(false);
    };

    run();
  }, [isSessionReady, jobId, router, user]);

  const handleExport = async () => {
    if (!jobId || isExporting) return;

    setIsExporting(true);
    setExportMessage("");
    const candidateIds = items.filter((item) => item.status !== "shortlisted").map((item) => item.candidateId);
    const result = await exportCandidates({ jobId, candidateIds, provider: "merge" });

    if (!result.success || !result.data) {
      setExportMessage(result.error || "Failed to export candidates.");
      setIsExporting(false);
      return;
    }

    setExportMessage(`Export ${result.data.status}: ${result.data.exportedCount} candidates (${result.data.reference})`);
    setIsExporting(false);
  };

  return (
    <AppShell activeStep={5}>
      <Card className="mx-auto w-full max-w-[560px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Candidates ready for interview</CardTitle>
          <CardDescription>Final shortlist calibrated to your hiring priorities</CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {isLoading && <p className="text-sm text-gray-600">Loading...</p>}

          {items.map((item) => (
            <div key={item.candidateId} className="space-y-3 rounded-xl border border-[#E5E7EB] bg-white p-4">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="font-semibold text-gray-900">{item.candidateId.slice(0, 8)}</p>
                </div>
                <Badge
                  variant={
                    item.status === "interview_scheduled"
                      ? "high"
                      : item.status === "contacted"
                        ? "medium"
                        : "neutral"
                  }
                >
                  {item.status}
                </Badge>
              </div>
              <Button className="w-full justify-center" disabled={isLoading}>
                Schedule Interview
              </Button>
            </div>
          ))}

          {!isLoading && !error && items.length === 0 && (
            <div className="rounded-xl border border-[#E5E7EB] bg-gray-50 p-4 text-sm text-gray-600">
              No interview-ready candidates yet.
            </div>
          )}

          {error && <p className="text-sm text-red-600">{error}</p>}

          <Button className="w-full justify-center" onClick={handleExport} disabled={isLoading || isExporting || items.length === 0}>
            {isExporting ? "Exporting..." : "Export to ATS"}
          </Button>
          {exportMessage && <p className="text-sm text-gray-700">{exportMessage}</p>}
        </CardContent>
      </Card>
    </AppShell>
  );
}
