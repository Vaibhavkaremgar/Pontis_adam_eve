"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAppContext } from "@/context/AppContext";
import {
  getAdminDiagnostics,
  getAuditLogs,
  getAutomationJobs,
  getDeadLetters,
  getNotificationCenter,
  getOutreachAnalytics,
  getOperationalIntelligence,
  getPipelineAnalytics,
  getPipelineBoard,
  getRecruiterTasks,
  markNotificationRead,
  replayDeadLetter,
} from "@/lib/api/admin";

export default function AdminPageClient() {
  const { user, isSessionReady, jobId } = useAppContext();
  const [diagnostics, setDiagnostics] = useState<any>(null);
  const [deadLetters, setDeadLetters] = useState<any[]>([]);
  const [outreach, setOutreach] = useState<any>(null);
  const [auditLogs, setAuditLogs] = useState<any[]>([]);
  const [pipelineBoard, setPipelineBoard] = useState<any>(null);
  const [pipelineAnalytics, setPipelineAnalytics] = useState<any>(null);
  const [notifications, setNotifications] = useState<any[]>([]);
  const [automationJobs, setAutomationJobs] = useState<any[]>([]);
  const [tasks, setTasks] = useState<any[]>([]);
  const [intelligence, setIntelligence] = useState<any>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isSessionReady || !user) return;
    void Promise.all([
      getAdminDiagnostics(),
      getDeadLetters(),
      getOutreachAnalytics(jobId || undefined),
      getAuditLogs(25),
      getPipelineBoard(jobId || undefined),
      getPipelineAnalytics(jobId || undefined),
      getNotificationCenter(jobId || undefined),
      getAutomationJobs(),
      getRecruiterTasks(jobId || undefined),
      getOperationalIntelligence(jobId || undefined),
    ]).then(([diag, dead, out, audit, board, analytics, inbox, automation, taskList, intel]) => {
      if (diag.success && diag.data) setDiagnostics(diag.data);
      if (dead.success && dead.data) setDeadLetters(dead.data);
      if (out.success && out.data) setOutreach(out.data);
      if (audit.success && audit.data) setAuditLogs(audit.data);
      if (board.success && board.data) setPipelineBoard(board.data);
      if (analytics.success && analytics.data) setPipelineAnalytics(analytics.data);
      if (inbox.success && inbox.data) setNotifications(inbox.data);
      if (automation.success && automation.data) setAutomationJobs(automation.data);
      if (taskList.success && taskList.data) setTasks(taskList.data);
      if (intel.success && intel.data) setIntelligence(intel.data);
    });
  }, [isSessionReady, user, jobId]);

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

  const onReadNotification = async (item: any) => {
    if (!item.notificationKey || item.isRead) return;
    const result = await markNotificationRead(String(item.notificationKey));
    if (result.success) {
      const refreshed = await getNotificationCenter(jobId || undefined);
      if (refreshed.success && refreshed.data) setNotifications(refreshed.data);
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

        <div className="grid gap-4 md:grid-cols-4">
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
              <CardTitle>Enrichment</CardTitle>
              <CardDescription>Apollo resolution and contact discovery</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              <p>Total profiles: {diagnostics?.enrichment?.total ?? 0}</p>
              <p>Enriched or partial: {diagnostics?.enrichment?.enrichedOrPartial ?? 0}</p>
              <p>Pending or resolving: {diagnostics?.enrichment?.pendingOrResolving ?? 0}</p>
              <p>Failed or missing: {diagnostics?.enrichment?.failed ?? 0}</p>
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
              <p>Workflow tokens: {diagnostics?.workflowTokens?.active ?? 0} active</p>
              <p>Interview stages: {diagnostics?.interviews?.total ?? 0} tracked</p>
            </CardContent>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Pipeline Board</CardTitle>
              <CardDescription>Current ATS stages and pending recruiter actions.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {Object.entries(pipelineBoard?.counts || {}).slice(0, 8).map(([key, value]) => (
                  <div key={key} className="rounded-xl border p-3">
                    <p className="text-xs uppercase tracking-wide text-gray-500">{String(key).replace(/_/g, " ")}</p>
                    <p className="mt-1 text-2xl font-semibold text-gray-900">{String(value)}</p>
                  </div>
                ))}
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-xl border p-3">
                  <p className="font-medium text-gray-900">Pending actions</p>
                  <div className="mt-3 space-y-2">
                    {(pipelineBoard?.pendingActions || []).slice(0, 5).map((item: any) => (
                      <div key={`${String(item.id || "")}`} className="rounded-lg bg-gray-50 p-2 text-sm">
                        <p className="font-medium text-gray-900">{String(item.title || item.type || "")}</p>
                        <p className="text-gray-600">{String(item.candidateId || "")}</p>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="rounded-xl border p-3">
                  <p className="font-medium text-gray-900">Upcoming interviews</p>
                  <div className="mt-3 space-y-2">
                    {(pipelineBoard?.upcomingInterviews || []).slice(0, 5).map((item: any) => (
                      <div key={`${String(item.candidateId || "")}-${String(item.stage || "")}`} className="rounded-lg bg-gray-50 p-2 text-sm">
                        <p className="font-medium text-gray-900">{String(item.name || item.candidateId || "")}</p>
                        <p className="text-gray-600">{String(item.stage || item.status || "")}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Notifications</CardTitle>
              <CardDescription>Unread recruiter and candidate activity.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {notifications.length === 0 ? (
                <p className="text-sm text-gray-600">No notifications.</p>
              ) : (
                notifications.slice(0, 8).map((item) => (
                  <div key={String(item.id)} className="rounded-xl border p-3 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <Badge variant={item.isRead ? "neutral" : "high"}>{item.isRead ? "Read" : "Unread"}</Badge>
                      <span className="text-xs text-gray-500">{String(item.channel || "")}</span>
                    </div>
                    <p className="mt-2 font-medium text-gray-900">{String(item.title || "")}</p>
                    <p className="text-gray-600">{String(item.body || "")}</p>
                    {!item.isRead ? (
                      <Button className="mt-2" variant="outline" onClick={() => void onReadNotification(item)}>
                        Mark read
                      </Button>
                    ) : null}
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle>Engagement</CardTitle>
              <CardDescription>Candidate responsiveness and momentum.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              <p>Score: {intelligence?.engagement?.engagementScore ?? 0}</p>
              <p>Momentum: {intelligence?.engagement?.momentum ?? "n/a"}</p>
              <p>Replies: {intelligence?.engagement?.replyCount ?? 0}</p>
              <p>Unread notifications: {intelligence?.engagement?.unreadNotifications ?? 0}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Interview Intelligence</CardTitle>
              <CardDescription>Quality and consistency signals.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              <p>Quality: {intelligence?.interview?.interviewQualityScore ?? 0}</p>
              <p>Consistency: {intelligence?.interview?.consistencyScore ?? 0}</p>
              <p>Recommendation: {intelligence?.interview?.recommendationSignal ?? "n/a"}</p>
              <p>Evaluations: {intelligence?.interview?.evaluationCount ?? 0}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Operational Alerts</CardTitle>
              <CardDescription>Stuck work, replay risk, and automation failures.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              <p>Anomalies: {intelligence?.anomalies?.length ?? 0}</p>
              <p>Coordination: {intelligence?.coordination?.coordinationMode ?? "dashboard"}</p>
              <p>Reactivation suggestions: {intelligence?.reactivation?.length ?? 0}</p>
              <p>Calendar suggestions: {intelligence?.calendar?.slotSuggestions?.length ?? 0}</p>
              <p>Missing workflow links: {diagnostics?.interviews?.missingWorkflowLinkage ?? 0}</p>
              <div className="mt-3 space-y-2">
                {(intelligence?.anomalies || []).slice(0, 3).map((item: any, index: number) => (
                  <div key={`${String(item.type || "anomaly")}-${index}`} className="rounded-lg bg-gray-50 p-2 text-xs">
                    <p className="font-medium text-gray-900">{String(item.type || "")}</p>
                    <p className="text-gray-600">{String(item.candidateId || item.jobId || "")}</p>
                  </div>
                ))}
                {(intelligence?.calendar?.slotSuggestions || []).slice(0, 2).map((item: any, index: number) => (
                  <div key={`${String(item.scheduledAt || "slot")}-${index}`} className="rounded-lg bg-blue-50 p-2 text-xs">
                    <p className="font-medium text-blue-900">{String(item.label || "")}</p>
                    <p className="text-blue-700">{String(item.timezone || "")}</p>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle>Analytics</CardTitle>
              <CardDescription>Pipeline conversion snapshot.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              <p>Candidates: {pipelineAnalytics?.candidateCount ?? 0}</p>
              <p>Interviews scheduled: {pipelineAnalytics?.interviewsScheduled ?? 0}</p>
              <p>Interviews completed: {pipelineAnalytics?.interviewsCompleted ?? 0}</p>
              <p>Evaluations submitted: {pipelineAnalytics?.evaluationsSubmitted ?? 0}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Automation</CardTitle>
              <CardDescription>Persistent background jobs.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-gray-700">
              <p>Jobs tracked: {automationJobs.length}</p>
              <p>Open tasks: {tasks.length}</p>
              <p>Notes: {pipelineBoard?.notesCount ?? 0}</p>
              <p>Automation queue: {pipelineBoard?.automation?.length ?? 0}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Tasks</CardTitle>
              <CardDescription>Recruiter follow-ups and reminders.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {tasks.length === 0 ? (
                <p className="text-sm text-gray-600">No open tasks.</p>
              ) : (
                tasks.slice(0, 5).map((task) => (
                  <div key={String(task.id)} className="rounded-lg border p-3 text-sm">
                    <p className="font-medium text-gray-900">{String(task.title || "")}</p>
                    <p className="text-gray-600">{String(task.body || "")}</p>
                    <p className="text-xs text-gray-500">{String(task.priority || "")}</p>
                  </div>
                ))
              )}
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
