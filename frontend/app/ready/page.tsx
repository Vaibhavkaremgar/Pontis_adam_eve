"use client";

/**
 * What this file does:
 * Displays interview-ready candidates with real names and statuses.
 * Allows ATS export. Schedule Interview navigates to a calendar link.
 *
 * What API it connects to:
 * GET /interviews?jobId=...
 * POST /candidates/export
 */
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAppContext } from "@/context/AppContext";
import { exportCandidates } from "@/lib/api/candidates";
import { getInterviewStatuses } from "@/lib/api/interviews";
import { getMetrics } from "@/lib/api/metrics";
import type { InterviewStatus } from "@/types";

const STATUS_LABELS: Record<string, string> = {
  shortlisted: "Shortlisted",
  contacted: "Contacted",
  interview_scheduled: "Interview Scheduled",
  interview_invited: "Interview Invited",
  booked: "Booked",
  rejected: "Rejected",
  exported: "Exported"
};

function formatStatus(status: InterviewStatus["status"] | string | null | undefined): string {
  const normalized = (status || "Unknown").toString().trim();
  if (!normalized) return "Unknown";
  return STATUS_LABELS[normalized] || normalized.replace(/_/g, " ");
}

export default function ReadyPage() {
  const router = useRouter();
  const { user, isSessionReady, jobId } = useAppContext();
  const [items, setItems] = useState<InterviewStatus[]>([]);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const [isExporting, setIsExporting] = useState(false);
  const [metrics, setMetrics] = useState<{
    emails_sent: number;
    replies_received: number;
    interviews_booked: number;
    conversion_rate: number;
  } | null>(null);
  const orderedItems = useMemo(() => {
    const priority: Record<string, number> = {
      contacted: 0,
      shortlisted: 1,
      rejected: 2
    };

    return [...items].sort((a, b) => {
      const aRank = priority[a.status] ?? 3;
      const bRank = priority[b.status] ?? 3;
      if (aRank !== bRank) return aRank - bRank;
      return a.name.localeCompare(b.name) || a.candidateId.localeCompare(b.candidateId);
    });
  }, [items]);

  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) { router.replace("/login"); return; }
    if (!jobId) { router.replace("/job"); return; }

    const run = async () => {
      setIsLoading(true);
      setError("");
      const [result, metricsResult] = await Promise.all([getInterviewStatuses(jobId), getMetrics()]);
      if (!result.success || !result.data) {
        setError(result.error || "Could not load interview statuses.");
      } else {
        setItems(result.data);
      }
      if (metricsResult.success && metricsResult.data) {
        setMetrics({
          emails_sent: metricsResult.data.emails_sent,
          replies_received: metricsResult.data.replies_received,
          interviews_booked: metricsResult.data.interviews_booked,
          conversion_rate: metricsResult.data.conversion_rate
        });
      }
      setIsLoading(false);
    };

    run();
  }, [isSessionReady, jobId, router, user]);

  const handleExport = async () => {
    if (!jobId || isExporting) return;
    setIsExporting(true);
    setExportMessage("");
    const candidateIds = orderedItems
      .filter((item) => !["rejected"].includes(item.status))
      .map((item) => item.candidateId);
    const result = await exportCandidates({ jobId, candidateIds });
    if (!result.success || !result.data) {
      setExportMessage(result.error || "Failed to export candidates.");
      setIsExporting(false);
      return;
    }
    setExportMessage(
      `Export ${result.data.status}: ${result.data.exportedCount} candidate${result.data.exportedCount !== 1 ? "s" : ""} (ref: ${result.data.reference})`
    );
    setIsExporting(false);
  };

  const statusVariant = (status: InterviewStatus["status"]) => {
    if (status === "interview_invited") return "info";
    if (status === "interview_scheduled") return "high";
    if (status === "booked") return "high";
    if (status === "contacted") return "medium";
    if (status === "rejected") return "low";
    return "neutral";
  };

  return (
    <AppShell activeStep={6}>
      <Card className="mx-auto w-full max-w-[560px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Candidates ready for interview</CardTitle>
          <CardDescription>Final shortlist calibrated to your hiring priorities</CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {isLoading && <p className="text-sm text-gray-600">Loading interview statuses...</p>}

          {!isLoading && !error && items.length === 0 && (
            <div className="rounded-xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4 text-sm text-gray-600">
              No replies yet. Outreach is still warming up or no candidate has booked an interview.
            </div>
          )}

          {orderedItems.map((item) => (
            <div key={item.candidateId} className="space-y-3 rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] p-4">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="font-semibold text-gray-900">
                    {item.name || item.candidateId.slice(0, 8)}
                  </p>
                  <p className="text-xs text-gray-500">{item.candidateId.slice(0, 12)}…</p>
                </div>
                <Badge variant={statusVariant(item.status)}>{formatStatus(item.status)}</Badge>
              </div>
              <Button
                className="w-full justify-center"
                variant="outline"
                disabled={isLoading}
                onClick={() => {
                  // Opens a mailto as a lightweight scheduling action.
                  // Replace with a real calendar integration (Calendly, etc.) when available.
                  window.open(
                    `mailto:?subject=Interview%20invitation&body=Hi%20${encodeURIComponent(item.name || "there")}%2C%0A%0AWe%27d%20love%20to%20schedule%20an%20interview.%20Please%20let%20us%20know%20your%20availability.`,
                    "_blank"
                  );
                }}
              >
                Schedule Interview
              </Button>
            </div>
          ))}

          {metrics && (
            <div className="grid grid-cols-2 gap-3 rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4 text-sm">
              <div>
                <p className="text-gray-500">Emails sent</p>
                <p className="font-semibold text-gray-900">{metrics.emails_sent}</p>
              </div>
              <div>
                <p className="text-gray-500">Replies received</p>
                <p className="font-semibold text-gray-900">{metrics.replies_received}</p>
              </div>
              <div>
                <p className="text-gray-500">Interviews booked</p>
                <p className="font-semibold text-gray-900">{metrics.interviews_booked}</p>
              </div>
              <div>
                <p className="text-gray-500">Conversion</p>
                <p className="font-semibold text-gray-900">{(metrics.conversion_rate * 100).toFixed(0)}%</p>
              </div>
            </div>
          )}

          {error && <p className="text-sm text-red-600">{error}</p>}

          <Button
            className="w-full justify-center"
            onClick={handleExport}
          disabled={isLoading || isExporting || orderedItems.length === 0}
          >
            {isExporting ? "Exporting..." : "Export to ATS"}
          </Button>
          {exportMessage && <p className="text-sm text-gray-700">{exportMessage}</p>}

          <Button
            variant="outline"
            className="w-full justify-center"
            onClick={() => router.push("/outreach")}
          >
            ← Back to Outreach
          </Button>
        </CardContent>
      </Card>
    </AppShell>
  );
}
