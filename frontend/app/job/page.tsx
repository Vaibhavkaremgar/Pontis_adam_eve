"use client";

/**
 * What this file does:
 * Captures job brief, creates hiring session, and loads initial candidate results.
 *
 * What API it connects to:
 * Uses /lib/api/hiring -> POST /hiring/create and /lib/api/candidates -> GET /candidates.
 *
 * How it fits in the pipeline:
 * This is the trigger step for backend embedding/vector workflow and candidate retrieval.
 */
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { CandidateModal } from "@/components/modals/candidate-modal";
import { LoadingModal } from "@/components/modals/loading-modal";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { useAppContext } from "@/context/AppContext";
import { getCandidatesWithMode } from "@/lib/api/candidates";
import { createHiring } from "@/lib/api/hiring";

export default function JobPage() {
  const router = useRouter();
  const { user, isSessionReady, company, job, setJob, setJobId, setCandidates, setIsRefined } = useAppContext();

  const [form, setForm] = useState(job);
  const [showLoading, setShowLoading] = useState(false);
  const [showCandidates, setShowCandidates] = useState(false);
  const [progress, setProgress] = useState(8);
  const [error, setError] = useState("");
  const [scoringMode, setScoringMode] = useState<"volume" | "elite">("volume");

  useEffect(() => {
    if (!isSessionReady) return;

    if (!user) {
      router.replace("/login");
      return;
    }

    if (!company.name.trim() || !company.website.trim()) {
      router.replace("/company");
    }
  }, [company.name, company.website, isSessionReady, router, user]);

  const canSubmit = useMemo(() => {
    return Boolean(form.title.trim() && form.description.trim()) && !showLoading;
  }, [form.description, form.title, showLoading]);

  const handleGenerateCandidates = async () => {
    // This handles real-world API delays and failures.
    if (!canSubmit) {
      setError("Job title and job description are required.");
      return;
    }

    setError("");
    setShowCandidates(false);
    setShowLoading(true);
    setProgress(25);

    const cleanJob = {
      ...form,
      title: form.title.trim(),
      description: form.description.trim(),
      location: form.location.trim(),
      compensation: form.compensation.trim()
    };

    setJob(cleanJob);
    setIsRefined(false);

    const createResult = await createHiring({
      company,
      job: cleanJob
    });

    if (!createResult.success || !createResult.data) {
      setShowLoading(false);
      setError(createResult.error || "Failed to create hiring session.");
      return;
    }

    const { jobId } = createResult.data;
    setJobId(jobId);
    setProgress(60);

    const candidatesResult = await getCandidatesWithMode({ jobId, mode: scoringMode, refresh: true });
    if (!candidatesResult.success || !candidatesResult.data) {
      setShowLoading(false);
      setError(candidatesResult.error || "Failed to load candidates.");
      return;
    }

    setCandidates(candidatesResult.data);
    setProgress(100);
    setShowLoading(false);
    setShowCandidates(true);
  };

  return (
    <AppShell activeStep={2}>
      <Card className="mx-auto w-full max-w-[560px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Define your job brief</CardTitle>
          <CardDescription>
            Share role context so backend AI can build embeddings and rank candidates.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
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
              onChange={(event) =>
                setForm((prev) => ({
                  ...prev,
                  description: event.target.value
                }))
              }
              disabled={showLoading}
            />
          </div>

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
              onChange={(event) =>
                setForm((prev) => ({
                  ...prev,
                  compensation: event.target.value
                }))
              }
              disabled={showLoading}
            />
          </div>

          <div className="space-y-3">
            <Label>Work Authorization</Label>
            <RadioGroup
              value={form.workAuthorization}
              onValueChange={(value) =>
                setForm((prev) => ({
                  ...prev,
                  workAuthorization: value as "required" | "preferred" | "not-required"
                }))
              }
              className="gap-2"
              disabled={showLoading}
            >
              <div className="flex items-center gap-3 rounded-lg border border-[#E5E7EB] bg-white px-4 py-3">
                <RadioGroupItem value="required" id="required" />
                <Label htmlFor="required" className="cursor-pointer font-normal text-gray-600">
                  Required
                </Label>
              </div>
              <div className="flex items-center gap-3 rounded-lg border border-[#E5E7EB] bg-white px-4 py-3">
                <RadioGroupItem value="preferred" id="preferred" />
                <Label htmlFor="preferred" className="cursor-pointer font-normal text-gray-600">
                  Preferred
                </Label>
              </div>
              <div className="flex items-center gap-3 rounded-lg border border-[#E5E7EB] bg-white px-4 py-3">
                <RadioGroupItem value="not-required" id="not-required" />
                <Label htmlFor="not-required" className="cursor-pointer font-normal text-gray-600">
                  Not required
                </Label>
              </div>
            </RadioGroup>
          </div>

          <div className="space-y-3">
            <Label>Scoring Mode</Label>
            <RadioGroup
              value={scoringMode}
              onValueChange={(value) => setScoringMode(value as "volume" | "elite")}
              className="gap-2"
              disabled={showLoading}
            >
              <div className="flex items-center gap-3 rounded-lg border border-[#E5E7EB] bg-white px-4 py-3">
                <RadioGroupItem value="volume" id="volume" />
                <Label htmlFor="volume" className="cursor-pointer font-normal text-gray-600">
                  Volume mode (fast scoring)
                </Label>
              </div>
              <div className="flex items-center gap-3 rounded-lg border border-[#E5E7EB] bg-white px-4 py-3">
                <RadioGroupItem value="elite" id="elite" />
                <Label htmlFor="elite" className="cursor-pointer font-normal text-gray-600">
                  Elite mode (deep reasoning)
                </Label>
              </div>
            </RadioGroup>
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <Button className="w-full justify-center" onClick={handleGenerateCandidates} disabled={!canSubmit}>
            {showLoading ? "Loading..." : "See Candidates"}
          </Button>
        </CardContent>
      </Card>

      <LoadingModal open={showLoading} progress={progress} />
      <CandidateModal open={showCandidates} onOpenChange={setShowCandidates} />
    </AppShell>
  );
}
