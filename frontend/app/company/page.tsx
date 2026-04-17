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

export default function CompanyPage() {
  const router = useRouter();
  const { user, isSessionReady, company, setCompany } = useAppContext();
  const [form, setForm] = useState(company);
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!isSessionReady) return;

    if (!user) {
      router.replace("/login");
    }
  }, [isSessionReady, router, user]);

  const handleSubmit = async () => {
    // This handles real-world API delays and failures.
    if (!form.name.trim() || !form.website.trim()) {
      setError("Company name and website are required.");
      return;
    }

    setIsSubmitting(true);
    setError("");

    setCompany({
      name: form.name.trim(),
      website: form.website.trim(),
      description: form.description.trim()
    });

    router.push("/job");
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

          <div className="rounded-xl border border-[#E5E7EB] bg-gray-50 p-4 text-sm text-gray-600">
            Frontend stores only recruiter-entered form fields. Embeddings and AI logic remain backend-only.
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
