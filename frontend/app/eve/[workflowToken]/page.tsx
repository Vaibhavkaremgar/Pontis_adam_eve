"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { initialCompany, initialJob, useAppContext } from "@/context/AppContext";
import { getEveWorkflowContext } from "@/lib/api/workflow";

function EveWorkflowBridge() {
  const router = useRouter();
  const params = useParams<{ workflowToken: string }>();
  const workflowToken = String(params?.workflowToken || "").trim();
  const { isSessionReady, user, setCompany, setJob, setJobId, setIsRefined } = useAppContext();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    if (!workflowToken) {
      setError("Missing workflow token.");
      setLoading(false);
      return;
    }

    let cancelled = false;
    const load = async () => {
      const result = await getEveWorkflowContext(workflowToken);
      if (cancelled) return;
      if (!result.success || !result.data) {
        setError(result.error || "Could not open this workflow link.");
        setLoading(false);
        return;
      }

      const nextJob = result.data.job || initialJob;
      const nextCompany = result.data.company || initialCompany;
      setJobId(result.data.jobId || "");
      setJob({ ...initialJob, ...nextJob });
      setCompany({ ...initialCompany, ...nextCompany });
      setIsRefined(true);
      router.replace("/review");
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [isSessionReady, router, setCompany, setIsRefined, setJob, setJobId, user, workflowToken]);

  if (error) {
    return (
      <AppShell activeStep={4}>
        <div className="mx-auto flex min-h-[40vh] w-full max-w-2xl items-center justify-center px-4">
          <div className="w-full rounded-3xl border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] p-6 text-center">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-[#166534]">Workflow link</p>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight text-gray-900">Unable to open Eve workflow</h1>
            <p className="mt-2 text-sm text-gray-600">{error}</p>
            <div className="mt-5 flex justify-center gap-3">
              <Button variant="outline" onClick={() => router.push("/workspace")}>
                Back to workspace
              </Button>
              <Button onClick={() => router.refresh()}>Retry</Button>
            </div>
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell activeStep={4}>
      <div className="mx-auto flex min-h-[40vh] w-full max-w-2xl items-center justify-center px-4">
        <div className="w-full rounded-3xl border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] p-6 text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-[#166534]">Workflow link</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-gray-900">
            {loading ? "Opening secure Eve workflow..." : "Redirecting to review..."}
          </h1>
          <p className="mt-2 text-sm text-gray-600">
            {loading ? "We are loading the job context without exposing the raw job id." : "Your recruiter workspace is ready."}
          </p>
        </div>
      </div>
    </AppShell>
  );
}

export default function EveWorkflowPage() {
  return <EveWorkflowBridge />;
}
