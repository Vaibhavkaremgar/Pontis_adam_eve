"use client";

/**
 * What this file does:
 * Captures the recruiter job brief, ATS connection state, and optional URL imports
 * before creating the hiring session.
 *
 * What API it connects to:
 * POST /api/ats/connect, POST /api/jobs/parse, POST /api/hiring/create
 *
 * How it fits in the pipeline:
 * This is the control panel that prepares the job, connects ATS, and launches
 * voice intake for the review flow.
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { LoadingModal } from "@/components/modals/loading-modal";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { connectAts, disconnectAts } from "@/lib/api/ats";
import { getCompany } from "@/lib/api/company";
import { parseJobPosting } from "@/lib/api/jobs";
import { createHiring } from "@/lib/api/hiring";
import { useAppContext } from "@/context/AppContext";

type AtsProviderKey = "mock" | "greenhouse" | "lever";
type RemotePolicyValue = "remote" | "hybrid" | "onsite";
type WorkAuthorizationValue = "required" | "preferred" | "not-required";

const ATS_LABELS: Record<AtsProviderKey, string> = {
  mock: "Mock ATS",
  greenhouse: "Greenhouse (Coming Soon)",
  lever: "Lever (Coming Soon)"
};

const REMOTE_POLICY_OPTIONS: Array<{ value: RemotePolicyValue; label: string }> = [
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "On-site" }
];

const WORK_AUTH_OPTIONS: Array<{ value: WorkAuthorizationValue; label: string }> = [
  { value: "required", label: "Required" },
  { value: "preferred", label: "Preferred" },
  { value: "not-required", label: "Not required" }
];

function formatAtsLabel(provider: string) {
  return ATS_LABELS[(provider as AtsProviderKey) || "mock"] || "Mock ATS";
}

export default function JobPage() {
  const router = useRouter();
  const { user, isSessionReady, company, job, setJob, setJobId, setCandidates, setVoiceNotes, setIsRefined, setCompany } =
    useAppContext();

  const [form, setForm] = useState(() => job);
  const [jobUrl, setJobUrl] = useState("");
  const [selectedATS, setSelectedATS] = useState<AtsProviderKey>(() => (company.atsProvider as AtsProviderKey) || "mock");
  const [atsConnected, setAtsConnected] = useState(() => Boolean(company.atsConnected));
  const [autoExport, setAutoExport] = useState(() => Boolean(job.autoExportToAts));
  const [showLoading, setShowLoading] = useState(false);
  const [progress, setProgress] = useState(8);
  const [submitError, setSubmitError] = useState("");
  const [atsMessage, setAtsMessage] = useState("");
  const [importMessage, setImportMessage] = useState("");
  const [isAtsLoading, setIsAtsLoading] = useState(true);
  const [isConnectingAts, setIsConnectingAts] = useState(false);
  const [isParsingJob, setIsParsingJob] = useState(false);
  const [scoringMode, setScoringMode] = useState<"volume" | "elite">("volume");

  useEffect(() => {
    let cancelled = false;

    const loadAtsState = async () => {
      if (!isSessionReady) return;
      if (!user) {
        router.replace("/login");
        return;
      }

      setIsAtsLoading(true);
      const result = await getCompany();
      if (cancelled) return;

      if (result.success && result.data) {
        const provider = (result.data.atsProvider || result.data.ats_provider || "mock") as AtsProviderKey;
        const connected = Boolean(result.data.atsConnected ?? result.data.ats_connected);
        const nextCompany = {
          name: result.data.name || "",
          website: result.data.website || "",
          description: result.data.description || "",
          industry: result.data.industry || "",
          atsProvider: provider,
          atsConnected: connected
        };
        setCompany(nextCompany);
        setSelectedATS(provider || "mock");
        setAtsConnected(connected);
        if (!nextCompany.name.trim() || !nextCompany.website.trim()) {
          router.replace("/company");
        }
      } else {
        router.replace("/company");
      }

      setIsAtsLoading(false);
    };

    loadAtsState();

    return () => {
      cancelled = true;
    };
  }, [isSessionReady, router, setCompany, user]);

  const canSubmit = Boolean(form.title.trim() && form.description.trim()) && !showLoading;

  const handleConnectAts = async () => {
    setAtsMessage("");
    setIsConnectingAts(true);

    const result = await connectAts({ provider: selectedATS });

    setIsConnectingAts(false);
    if (!result.success || !result.data) {
      setAtsMessage(result.error || "Could not connect ATS right now.");
      return;
    }

    const provider = (result.data.provider || selectedATS) as AtsProviderKey;
    setSelectedATS(provider);
    setAtsConnected(true);
    setCompany({
      ...company,
      atsProvider: provider,
      atsConnected: true
    });
    setAtsMessage(`Connected to: ${formatAtsLabel(provider)} ✅`);
  };

  const handleDisconnectAts = async () => {
    setAtsMessage("");
    setIsConnectingAts(true);

    const result = await disconnectAts();
    setIsConnectingAts(false);

    if (!result.success || !result.data) {
      setAtsMessage(result.error || "Could not disconnect ATS right now.");
      return;
    }

    setSelectedATS("mock");
    setAtsConnected(false);
    setCompany({
      ...company,
      atsProvider: "",
      atsConnected: false
    });
    setAtsMessage("ATS disconnected.");
  };

  const handleParseJob = async () => {
    const url = jobUrl.trim();
    if (!url) {
      setImportMessage("Paste a job posting URL first.");
      return;
    }

    setImportMessage("");
    setIsParsingJob(true);

    const result = await parseJobPosting({ url });

    setIsParsingJob(false);
    if (!result.success || !result.data) {
      setImportMessage(result.error || "Failed to parse the URL.");
      return;
    }

    const parsed = result.data;
    setForm((prev) => ({
      ...prev,
      title: parsed.title || prev.title,
      description: parsed.description || prev.description,
      location: parsed.location || prev.location,
      compensation: parsed.compensation || prev.compensation,
      workAuthorization: parsed.workAuthorization || prev.workAuthorization,
      remotePolicy: parsed.remotePolicy || prev.remotePolicy,
      experienceRequired: parsed.experienceRequired || prev.experienceRequired
    }));
    setImportMessage("Job fields were prefilled from the URL.");
  };

  const handleGenerateCandidates = async () => {
    if (!canSubmit) {
      setSubmitError("Job title and job description are required.");
      return;
    }

    setSubmitError("");
    setShowLoading(true);
    setProgress(25);

    const cleanJob = {
      ...form,
      title: form.title.trim(),
      description: form.description.trim(),
      location: form.location.trim(),
      compensation: form.compensation.trim(),
      remotePolicy: (form.remotePolicy || "hybrid").trim(),
      experienceRequired: (form.experienceRequired || "").trim(),
      vettingMode: scoringMode,
      autoExportToAts: Boolean(autoExport)
    };

    setJob(cleanJob);
    setCandidates([]);
    setVoiceNotes([]);
    setIsRefined(false);

    const createResult = await createHiring({ company, job: cleanJob });
    if (!createResult.success || !createResult.data) {
      setShowLoading(false);
      setSubmitError(createResult.error || "Failed to create hiring session.");
      return;
    }

    const { jobId } = createResult.data;
    setJobId(jobId);
    setProgress(100);
    router.push("/voice");
  };

  return (
    <AppShell activeStep={2}>
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-6">
        <div className="rounded-3xl border border-[rgba(120,100,80,0.08)] bg-gradient-to-br from-[#F6F1E8] via-[#F3EDE3] to-[#EFE6D8] p-6 shadow-[0_4px_12px_rgba(0,0,0,0.02)]">
          <div className="flex flex-col gap-2">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-[#166534]">Recruiter workflow</p>
            <h1 className="text-2xl font-semibold tracking-tight text-gray-900">Create a job and wire ATS in one flow</h1>
            <p className="max-w-2xl text-sm text-gray-600">
              Connect your ATS, import a posting if you have one, then finish the brief and launch candidate generation.
            </p>
          </div>
        </div>

        <Card className="overflow-hidden border-[rgba(120,100,80,0.08)]">
          <CardHeader className="border-b border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] px-6 py-5">
            <CardTitle>ATS Integration</CardTitle>
            <CardDescription>Connect your ATS to export shortlisted candidates automatically.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5 px-6 py-6">
            {isAtsLoading ? (
              <p className="text-sm text-gray-600">Loading ATS status...</p>
            ) : atsConnected ? (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="rounded-full border border-[#D1FAE5] bg-[#F0FDF4] px-3 py-1 font-medium text-[#166534]">
                    Connected to: {formatAtsLabel(selectedATS)} ✅
                  </span>
                </div>
                <Button variant="outline" onClick={handleDisconnectAts} disabled={isConnectingAts || showLoading}>
                  {isConnectingAts ? "Disconnecting..." : "Disconnect"}
                </Button>
              </div>
            ) : (
              <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
                <div className="flex-1 space-y-2">
                  <Label htmlFor="ats-provider">Select ATS</Label>
                  <select
                    id="ats-provider"
                    value={selectedATS}
                    onChange={(event) => setSelectedATS(event.target.value as AtsProviderKey)}
                    disabled={isConnectingAts || showLoading}
                    className="flex h-12 w-full rounded-xl border border-[rgba(120,100,80,0.08)] bg-[#F5EFE6] px-4 text-sm text-gray-700 outline-none transition focus:ring-2 focus:ring-green-900/15 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <option value="mock">Mock ATS</option>
                    <option value="greenhouse">Greenhouse (Coming Soon)</option>
                    <option value="lever">Lever (Coming Soon)</option>
                  </select>
                </div>
                <div className="lg:min-w-[180px]">
                  <Button className="w-full" onClick={handleConnectAts} disabled={isConnectingAts || showLoading}>
                    {isConnectingAts ? "Connecting..." : "Connect ATS"}
                  </Button>
                </div>
              </div>
            )}

            <label className="flex cursor-pointer items-start gap-3 rounded-2xl border border-dashed border-[#CBD5E1] bg-[#F8FAFC] p-4">
              <input
                id="auto-export"
                type="checkbox"
                checked={autoExport}
                onChange={(event) => setAutoExport(event.target.checked)}
                disabled={showLoading}
                className="mt-1 h-4 w-4 rounded border-gray-300 text-green-700 focus:ring-green-700"
              />
              <span className="space-y-1">
                <span className="block text-sm font-medium text-gray-900">Auto-send shortlisted candidates to ATS</span>
                <span className="block text-xs text-gray-500">
                  When enabled, candidates that reach shortlist will be exported automatically. The page still works if ATS
                  is not connected.
                </span>
              </span>
            </label>

            {atsMessage ? <p className="text-sm text-gray-700">{atsMessage}</p> : null}
          </CardContent>
        </Card>

        <Card className="overflow-hidden border-[rgba(120,100,80,0.08)]">
          <CardHeader className="border-b border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] px-6 py-5">
            <CardTitle>Import Job</CardTitle>
            <CardDescription>Paste a posting URL and let the parser prefill the core fields.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 px-6 py-6">
            <div className="flex flex-col gap-3 lg:flex-row">
              <div className="flex-1 space-y-2">
                <Label htmlFor="job-url">Import from Job Posting URL</Label>
                <Input
                  id="job-url"
                  placeholder="https://company.com/careers/senior-frontend-engineer"
                  value={jobUrl}
                  onChange={(event) => setJobUrl(event.target.value)}
                  disabled={isParsingJob || showLoading}
                />
              </div>
              <div className="lg:pt-8">
                <Button variant="secondary" onClick={handleParseJob} disabled={isParsingJob || showLoading}>
                  {isParsingJob ? "Parsing..." : "Parse"}
                </Button>
              </div>
            </div>
            {importMessage ? <p className="text-sm text-gray-700">{importMessage}</p> : null}
          </CardContent>
        </Card>

        <Card className="overflow-hidden border-[rgba(120,100,80,0.08)]">
          <CardHeader className="border-b border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] px-6 py-5">
            <CardTitle>Job Details</CardTitle>
            <CardDescription>Fill in the structured brief recruiters need before candidate generation starts.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5 px-6 py-6">
            <div className="space-y-2">
              <Label htmlFor="job-title">Job Title *</Label>
              <Input
                id="job-title"
                placeholder="Senior Frontend Engineer"
                value={form.title}
                onChange={(event) => setForm((prev) => ({ ...prev, title: event.target.value }))}
                disabled={showLoading}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="job-description">Job Description *</Label>
              <Textarea
                id="job-description"
                placeholder="Describe responsibilities, must-have skills, and desired outcomes."
                value={form.description}
                onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
                disabled={showLoading}
              />
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="location">Location</Label>
                <Input
                  id="location"
                  placeholder="San Francisco / Remote"
                  value={form.location}
                  onChange={(event) => setForm((prev) => ({ ...prev, location: event.target.value }))}
                  disabled={showLoading}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="compensation">Compensation</Label>
                <Input
                  id="compensation"
                  placeholder="$140k - $180k + Equity"
                  value={form.compensation}
                  onChange={(event) => setForm((prev) => ({ ...prev, compensation: event.target.value }))}
                  disabled={showLoading}
                />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="overflow-hidden border-[rgba(120,100,80,0.08)]">
          <CardHeader className="border-b border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] px-6 py-5">
            <CardTitle>Additional Details</CardTitle>
            <CardDescription>These help the backend rank candidates and keep the brief structured.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5 px-6 py-6">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="remote-policy">Remote Policy</Label>
                <select
                  id="remote-policy"
                  value={(form.remotePolicy as RemotePolicyValue) || "hybrid"}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, remotePolicy: event.target.value as RemotePolicyValue }))
                  }
                  disabled={showLoading}
                  className="flex h-12 w-full rounded-xl border border-[rgba(120,100,80,0.08)] bg-[#F5EFE6] px-4 text-sm text-gray-700 outline-none transition focus:ring-2 focus:ring-green-900/15 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {REMOTE_POLICY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="work-authorization">Work Authorization</Label>
                <select
                  id="work-authorization"
                  value={form.workAuthorization}
                  onChange={(event) =>
                    setForm((prev) => ({
                      ...prev,
                      workAuthorization: event.target.value as WorkAuthorizationValue
                    }))
                  }
                  disabled={showLoading}
                  className="flex h-12 w-full rounded-xl border border-[rgba(120,100,80,0.08)] bg-[#F5EFE6] px-4 text-sm text-gray-700 outline-none transition focus:ring-2 focus:ring-green-900/15 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {WORK_AUTH_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="experience-required">Experience Required</Label>
              <Input
                id="experience-required"
                placeholder="3-5 years"
                value={form.experienceRequired || ""}
                onChange={(event) => setForm((prev) => ({ ...prev, experienceRequired: event.target.value }))}
                disabled={showLoading}
              />
            </div>
          </CardContent>
        </Card>

        <Card className="border-[rgba(120,100,80,0.08)]">
          <CardContent className="space-y-4 px-6 py-6">
            <div className="space-y-3">
              <Label>Scoring Mode</Label>
              <div className="grid gap-3 md:grid-cols-2">
                {([
                  {
                    value: "volume" as const,
                    title: "Volume mode",
                    description: "Fast scoring for high-throughput sourcing."
                  },
                  {
                    value: "elite" as const,
                    title: "Elite mode",
                    description: "Deeper reasoning for tighter shortlists."
                  }
                ] as const).map((option) => (
                  <label
                    key={option.value}
                    className={`flex cursor-pointer items-start gap-3 rounded-2xl border px-4 py-4 transition ${
                      scoringMode === option.value
                        ? "border-green-700 bg-green-50/60"
                        : "border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] hover:bg-[#EFE6D8]"
                    }`}
                  >
                    <input
                      type="radio"
                      name="scoring-mode"
                      value={option.value}
                      checked={scoringMode === option.value}
                      onChange={() => setScoringMode(option.value)}
                      className="mt-1 h-4 w-4 border-gray-300 text-green-700 focus:ring-green-700"
                      disabled={showLoading}
                    />
                    <span className="space-y-1">
                      <span className="block text-sm font-medium text-gray-900">{option.title}</span>
                      <span className="block text-xs text-gray-500">{option.description}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>

            {submitError ? <p className="text-sm text-red-600">{submitError}</p> : null}

            <Button className="w-full justify-center" onClick={handleGenerateCandidates} disabled={!canSubmit}>
              {showLoading ? "Loading..." : "Continue → Voice Intake"}
            </Button>
          </CardContent>
        </Card>
      </div>

      <LoadingModal open={showLoading} progress={progress} />
    </AppShell>
  );
}
