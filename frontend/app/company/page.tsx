"use client";

/**
 * What this file does:
 * Collects company details (step 1), validates required fields, and stores them in global state.
 *
 * What API it connects to:
 * No direct API call in this step; data is forwarded to /hiring/create from Job step.
 *
 * How it fits in the pipeline:
 * Captures recruiter company context before backend candidate pipeline is triggered.
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useAppContext } from "@/context/AppContext";
import { connectAts, disconnectAts } from "@/lib/api/ats";
import { getCompany, saveCompany } from "@/lib/api/company";

export default function CompanyPage() {
  const router = useRouter();
  const { user, isSessionReady, company, setCompany } = useAppContext();
  const [form, setForm] = useState(company);
  const [error, setError] = useState("");
  const [connectError, setConnectError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState("mock");
  const [isConnecting, setIsConnecting] = useState(false);
  const [isAtsLoading, setIsAtsLoading] = useState(true);
  const [connectMessage, setConnectMessage] = useState("");

  useEffect(() => {
    if (!isSessionReady) return;

    if (!user) {
      router.replace("/login");
    }
  }, [isSessionReady, router, user]);

  useEffect(() => {
    let cancelled = false;

    const loadCompany = async () => {
      if (!isSessionReady || !user) return;

      setIsAtsLoading(true);
      const result = await getCompany();
      if (cancelled) return;

      if (result.success && result.data) {
        const nextProvider = result.data.atsProvider || result.data.ats_provider || "mock";
        const nextConnected = Boolean(result.data.atsConnected ?? result.data.ats_connected);
        const nextForm = {
          name: result.data.name || "",
          website: result.data.website || "",
          description: result.data.description || "",
          industry: result.data.industry || "",
          atsProvider: nextProvider,
          atsConnected: nextConnected
        };
        setCompany(nextForm);
        setForm(nextForm);
        setSelectedProvider(nextProvider || "mock");
      } else {
        setSelectedProvider("mock");
      }

      setIsAtsLoading(false);
    };

    loadCompany();

    return () => {
      cancelled = true;
    };
  }, [isSessionReady, setCompany, user]);

  const handleSubmit = async () => {
    // This handles real-world API delays and failures.
    if (!form.name.trim() || !form.website.trim()) {
      setError("Company name and website are required.");
      return;
    }

    setIsSubmitting(true);
    setError("");

    const result = await saveCompany({
      name: form.name.trim(),
      website: form.website.trim(),
      description: form.description.trim(),
      industry: (form.industry || "").trim()
    });
    if (result.success && result.data) {
      setCompany({
        name: result.data.name,
        website: result.data.website,
        description: result.data.description,
        industry: result.data.industry || "",
        atsProvider: result.data.atsProvider || result.data.ats_provider || "",
        atsConnected: Boolean(result.data.atsConnected ?? result.data.ats_connected)
      });
      router.push("/job");
      return;
    }

    setError(result.error || "Failed to save company details.");
    setIsSubmitting(false);
  };

  const handleConnectAts = async () => {
    if (!form.name.trim() || !form.website.trim()) {
      setError("Save company details first.");
      return;
    }

    setIsConnecting(true);
    setConnectMessage("");
    setConnectError("");

    const saved = await saveCompany({
      name: form.name.trim(),
      website: form.website.trim(),
      description: form.description.trim(),
      industry: (form.industry || "").trim()
    });
    if (!saved.success || !saved.data) {
      setConnectError(saved.error || "Failed to save company before connecting ATS.");
      setIsConnecting(false);
      return;
    }

    const connected = await connectAts({ provider: selectedProvider });
    if (!connected.success || !connected.data) {
      setError(connected.error || "Failed to connect ATS.");
      setIsConnecting(false);
      return;
    }

    const nextCompany = {
      name: saved.data.name,
      website: saved.data.website,
      description: saved.data.description,
      industry: saved.data.industry || "",
      atsProvider: connected.data.provider || selectedProvider,
      atsConnected: Boolean(connected.data.connected)
    };
    setCompany(nextCompany);
    setForm(nextCompany);
    setConnectMessage(`Connected to: ${connected.data.provider === "mock" ? "Mock ATS" : connected.data.provider}`);
    setIsConnecting(false);
  };

  const handleDisconnectAts = async () => {
    setIsConnecting(true);
    setConnectMessage("");

    const result = await disconnectAts();
    if (!result.success || !result.data) {
      setConnectError(result.error || "Failed to disconnect ATS.");
      setIsConnecting(false);
      return;
    }

    setCompany({
      ...company,
      atsProvider: "",
      atsConnected: false
    });
    setForm((prev) => ({
      ...prev,
      atsProvider: "",
      atsConnected: false
    }));
    setSelectedProvider("mock");
    setConnectMessage("ATS disconnected.");
    setIsConnecting(false);
  };

  return (
    <AppShell activeStep={1}>
      <Card className="mx-auto w-full max-w-[560px]">
        <CardHeader className="space-y-2 text-center">
          <CardTitle>Tell us about your company</CardTitle>
          <CardDescription>
            This context helps us tailor candidate matching and communication tone.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="space-y-2">
            <Label htmlFor="company-name">Company Name *</Label>
            <Input
              id="company-name"
              placeholder="Pontis Labs"
              value={form.name}
              onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
              disabled={isSubmitting}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="company-website">Website *</Label>
            <Input
              id="company-website"
              placeholder="https://example.com"
              value={form.website}
              onChange={(event) => setForm((prev) => ({ ...prev, website: event.target.value }))}
              disabled={isSubmitting}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="company-description">Description</Label>
            <Textarea
              id="company-description"
              placeholder="What does your company do and what is your hiring culture?"
              value={form.description}
              onChange={(event) =>
                setForm((prev) => ({
                  ...prev,
                  description: event.target.value
                }))
              }
              disabled={isSubmitting}
            />
          </div>

          <div className="rounded-xl border border-dashed border-[#CBD5E1] bg-slate-50 p-4">
            {isAtsLoading ? (
              <p className="text-sm text-gray-600">Loading ATS status...</p>
            ) : company.atsConnected ? (
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-gray-900">
                    Connected to: {company.atsProvider === "mock" ? "Mock ATS" : company.atsProvider}
                  </p>
                  <p className="text-xs text-gray-500">ATS stays connected after refresh until you disconnect it.</p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleDisconnectAts}
                  disabled={isSubmitting || isConnecting}
                >
                  {isConnecting ? "Disconnecting..." : "Disconnect"}
                </Button>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="grid gap-3 md:grid-cols-[1fr_auto] md:items-end">
                  <div className="space-y-2">
                    <Label htmlFor="ats-provider">Select ATS</Label>
                    <select
                      id="ats-provider"
                      value={selectedProvider}
                      onChange={(event) => setSelectedProvider(event.target.value)}
                      disabled={isSubmitting || isConnecting}
                      className="flex h-12 w-full rounded-xl border border-[rgba(120,100,80,0.08)] bg-[#F5EFE6] px-4 text-sm text-gray-700 outline-none transition focus:ring-2 focus:ring-green-900/15 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <option value="mock">Mock ATS</option>
                      <option value="greenhouse" disabled>
                        Greenhouse (Coming Soon)
                      </option>
                      <option value="lever" disabled>
                        Lever (Coming Soon)
                      </option>
                    </select>
                  </div>
                  <Button type="button" onClick={handleConnectAts} disabled={isSubmitting || isConnecting}>
                    {isConnecting ? "Connecting..." : "Connect ATS"}
                  </Button>
                </div>
                <p className="text-xs text-gray-500">Connect your ATS before creating jobs to enable auto-export.</p>
              </div>
            )}
            {connectMessage && <p className="mt-2 text-sm text-green-700">{connectMessage}</p>}
            {connectError && <p className="mt-2 text-sm text-red-600">{connectError}</p>}
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}

          <Button className="w-full justify-center" onClick={handleSubmit} disabled={isSubmitting}>
            {isSubmitting ? "Loading..." : "Continue"}
          </Button>
        </CardContent>
      </Card>
    </AppShell>
  );
}
