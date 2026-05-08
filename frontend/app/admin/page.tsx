"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAppContext } from "@/context/AppContext";
import { getAdminDiagnostics, getAuditLogs, getDeadLetters, getOutreachAnalytics, replayDeadLetter } from "@/lib/api/admin";

export default function AdminPage() {
  const { user, isSessionReady } = useAppContext();
  const [diagnostics, setDiagnostics] = useState<any>(null);
  const [deadLetters, setDeadLetters] = useState<any[]>([]);
  const [outreach, setOutreach] = useState<any>(null);
  const [auditLogs, setAuditLogs] = useState<any[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isSessionReady || !user) return;
    void Promise.all([getAdminDiagnostics(), getDeadLetters(), getOutreachAnalytics(), getAuditLogs(25)]).then(
      ([diag, dead, out, audit]) => {
        if (diag.success && diag.data) setDiagnostics(diag.data);
        if (dead.success && dead.data) setDeadLetters(dead.data);
        if (out.success && out.data) setOutreach(out.data);
        if (audit.success && audit.data) setAuditLogs(audit.data);
      }
    );
  }, [isSessionReady, user]);

  const onReplay = async (item: any) => {
    const result = await replayDeadLetter(String(item.queueType || ""), String(item.jobId || ""));
    if (result.success) {
      setMessage(`Replayed ${String(item.queueType || "")}:${String(item.jobId || "")}`);
      const refreshed = await getDeadLetters();
      if (refreshed.success && refreshed.data) setDeadLetters(refreshed.data);
    } else {
      setError(result.error || "Could not replay job.");
    }
  };

  return (
    <AppShell activeStep={0}>
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6">
        <div>
          <h1 className="text-3xl font-semibold text-gray-900">Platform Admin</h1>
          <p className="text-sm text-gray-600">Diagnostics, recovery, and operational visibility for Pontis.</p>
        </div>

        {(message || error) && (
          <Card>
            <CardContent className="pt-6">
              <p className={error ? "text-red-600" : "text-emerald-700"}>{error || message}</p>
            </CardContent>
          </Card>
        )}

        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle>Environment</CardTitle>
              <CardDescription>Startup config and service posture</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              <p>Env: {diagnostics?.config?.environment ?? "n/a"}</p>
              <p>Queue: {diagnostics?.queue?.status ?? "n/a"}</p>
              <p>LLM: {diagnostics?.llm?.status ?? "n/a"}</p>
              <p>Qdrant: {diagnostics?.qdrant?.status ?? "n/a"}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Outreach</CardTitle>
              <CardDescription>Deliverability and engagement</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              <p>Reply rate: {outreach?.replyRate ?? 0}</p>
              <p>Bounce rate: {outreach?.bounceRate ?? 0}</p>
              <p>Opened: {outreach?.counts?.opened ?? 0}</p>
              <p>Sent: {outreach?.counts?.sent ?? 0}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Queue</CardTitle>
              <CardDescription>Recovery and backpressure</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              <p>Dead letters: {deadLetters.length}</p>
              <p>Workers: {diagnostics?.queue?.workers ?? 0}</p>
              <p>Events: {diagnostics?.metrics?.events ?? 0}</p>
              <p>AI drifts: {diagnostics?.metrics?.ai_observability?.ranking_drifts ?? 0}</p>
            </CardContent>
          </Card>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle>AI Quality</CardTitle>
              <CardDescription>Retrieval and ranking signals</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              <p>Retrieval quality events: {diagnostics?.metrics?.ai_observability?.retrieval_quality_events ?? 0}</p>
              <p>Ranking regressions: {diagnostics?.metrics?.ai_observability?.ranking_regressions ?? 0}</p>
              <p>Embedding drift events: {diagnostics?.metrics?.ai_observability?.embedding_drift_events ?? 0}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Deliverability</CardTitle>
              <CardDescription>Outreach safety and reputation</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              <p>Reply rate: {outreach?.replyRate ?? 0}</p>
              <p>Bounce rate: {outreach?.bounceRate ?? 0}</p>
              <p>AI latency: {diagnostics?.metrics?.ai_observability?.avg_queue_ai_latency ?? 0}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Access</CardTitle>
              <CardDescription>Role-gated operational surfaces</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              <p>Signed in as: {user?.email ?? "unknown"}</p>
              <p>Role: {(user as any)?.role ?? "recruiter"}</p>
              <p>Admin tools: restricted</p>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Dead Letters</CardTitle>
            <CardDescription>Replay queue jobs after fixing the root cause.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {deadLetters.length === 0 ? (
              <p className="text-sm text-gray-600">No dead-letter jobs found.</p>
            ) : (
              <div className="space-y-3">
                {deadLetters.map((item, index) => (
                  <div key={`${String(item.queueType || "")}-${String(item.jobId || index)}`} className="flex items-center justify-between gap-4 rounded-xl border p-3">
                    <div className="min-w-0">
                      <p className="font-medium text-gray-900">{String(item.queueType || "")}</p>
                      <p className="text-sm text-gray-600 break-all">{String(item.jobId || "")}</p>
                      <p className="text-xs text-gray-500">{String(item.lastError || "")}</p>
                    </div>
                    <Button onClick={() => void onReplay(item)}>Replay</Button>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Recent Audit</CardTitle>
              <CardDescription>Latest recorded platform activity.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {auditLogs.slice(0, 8).map((row, index) => (
                <div key={index} className="rounded-lg border p-3 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium text-gray-900">{String(row.action || "")}</span>
                    <Badge variant="neutral">{String(row.entityType || "")}</Badge>
                  </div>
                  <p className="text-gray-600">{String(row.entityId || "")}</p>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Config</CardTitle>
              <CardDescription>Warnings and critical validation issues.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              {(diagnostics?.config?.warnings || []).length === 0 ? <p>No warnings.</p> : null}
              {(diagnostics?.config?.warnings || []).slice(0, 5).map((warning: any, index: number) => (
                <p key={index} className="text-amber-700">{String(warning.message || warning)}</p>
              ))}
              {(diagnostics?.config?.critical || []).slice(0, 5).map((issue: any, index: number) => (
                <p key={index} className="text-red-600">{String(issue.message || issue)}</p>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}
