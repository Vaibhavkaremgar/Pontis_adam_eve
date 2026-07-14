"use client";

import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Textarea } from "@/components/ui/textarea";

export type SecondRoundInviteValues = {
  roundType: "Second Round" | "Final Round";
  mode: "virtual" | "in_person";
  interviewDate: string;
  interviewTime: string;
  timezone: string;
  meetUrl: string;
  officeAddress: string;
  recruiterEmail: string;
  interviewerName: string;
  interviewerEmail: string;
  additionalNotes: string;
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  candidateName: string;
  role: string;
  company: string;
  defaultRecruiterEmail: string;
  submitting?: boolean;
  onSubmit: (values: SecondRoundInviteValues) => void;
};

const DEFAULT_VALUES: SecondRoundInviteValues = {
  roundType: "Second Round",
  mode: "virtual",
  interviewDate: "",
  interviewTime: "",
  timezone: "",
  meetUrl: "",
  officeAddress: "",
  recruiterEmail: "",
  interviewerName: "",
  interviewerEmail: "",
  additionalNotes: "",
};

export function SecondRoundSchedulingModal({
  open,
  onOpenChange,
  candidateName,
  role,
  company,
  defaultRecruiterEmail,
  submitting,
  onSubmit,
}: Props) {
  const [values, setValues] = useState<SecondRoundInviteValues>(DEFAULT_VALUES);

  useEffect(() => {
    if (!open) return;
    setValues((current) => ({
      ...DEFAULT_VALUES,
      recruiterEmail: current.recruiterEmail || defaultRecruiterEmail || "",
    }));
  }, [defaultRecruiterEmail, open]);

  const canSubmit = useMemo(() => {
    const required = [values.recruiterEmail, values.interviewDate, values.interviewTime, values.timezone, values.interviewerName, values.interviewerEmail]
      .map((item) => item.trim())
      .every(Boolean);
    if (!required) return false;
    return values.mode === "virtual" ? Boolean(values.meetUrl.trim()) : Boolean(values.officeAddress.trim());
  }, [values]);

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Schedule second round"
      description={`Send the next-round invite for ${candidateName || "this candidate"} without leaving the results page.`}
      className="max-w-3xl bg-white"
    >
      <div className="space-y-5">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Round Type</span>
            <select
              className="h-12 w-full rounded-xl border border-slate-200 bg-white px-4 text-sm outline-none focus:ring-2 focus:ring-sky-500/20"
              value={values.roundType}
              onChange={(event) => setValues((current) => ({ ...current, roundType: event.target.value as SecondRoundInviteValues["roundType"] }))}
            >
              <option value="Second Round">Second Round</option>
              <option value="Final Round">Final Round</option>
            </select>
          </label>
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Interview Mode</span>
            <select
              className="h-12 w-full rounded-xl border border-slate-200 bg-white px-4 text-sm outline-none focus:ring-2 focus:ring-sky-500/20"
              value={values.mode}
              onChange={(event) => setValues((current) => ({ ...current, mode: event.target.value as SecondRoundInviteValues["mode"] }))}
            >
              <option value="virtual">Virtual</option>
              <option value="in_person">In person</option>
            </select>
          </label>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Interview Date</span>
            <Input value={values.interviewDate} onChange={(event) => setValues((current) => ({ ...current, interviewDate: event.target.value }))} placeholder="2026-07-18" />
          </label>
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Interview Time</span>
            <Input value={values.interviewTime} onChange={(event) => setValues((current) => ({ ...current, interviewTime: event.target.value }))} placeholder="2:30 PM" />
          </label>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Timezone</span>
            <Input value={values.timezone} onChange={(event) => setValues((current) => ({ ...current, timezone: event.target.value }))} placeholder="Asia/Calcutta" />
          </label>
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">{values.mode === "virtual" ? "Google Meet URL" : "Location"}</span>
            {values.mode === "virtual" ? (
              <Input value={values.meetUrl} onChange={(event) => setValues((current) => ({ ...current, meetUrl: event.target.value }))} placeholder="https://meet.google.com/..." />
            ) : (
              <Textarea
                value={values.officeAddress}
                onChange={(event) => setValues((current) => ({ ...current, officeAddress: event.target.value }))}
                placeholder="123 Market Street, San Francisco, CA"
                className="min-h-[96px]"
              />
            )}
          </label>
        </div>

        <label className="space-y-2 text-sm">
          <span className="font-medium text-slate-700">Recruiter Email</span>
          <Input
            value={values.recruiterEmail}
            onChange={(event) => setValues((current) => ({ ...current, recruiterEmail: event.target.value }))}
            placeholder="recruiter@company.com"
          />
        </label>

        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Interviewer Name</span>
            <Input
              value={values.interviewerName}
              onChange={(event) => setValues((current) => ({ ...current, interviewerName: event.target.value }))}
              placeholder="Alex Morgan"
            />
          </label>
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Interviewer Email</span>
            <Input
              value={values.interviewerEmail}
              onChange={(event) => setValues((current) => ({ ...current, interviewerEmail: event.target.value }))}
              placeholder="alex@company.com"
            />
          </label>
        </div>

        <label className="space-y-2 text-sm">
          <span className="font-medium text-slate-700">Additional Notes</span>
          <Textarea
            value={values.additionalNotes}
            onChange={(event) => setValues((current) => ({ ...current, additionalNotes: event.target.value }))}
            placeholder="Add prep notes or any interviewer instructions."
            className="min-h-[120px]"
          />
        </label>

        <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
          <p className="font-medium text-slate-900">Summary</p>
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            <p>Candidate: {candidateName || "Unknown"}</p>
            <p>Role: {role || "Unknown"}</p>
            <p>Company: {company || "Unknown"}</p>
            <p>Mode: {values.mode === "virtual" ? "Virtual" : "In person"}</p>
          </div>
        </div>

        <div className="flex flex-wrap justify-end gap-3">
          <Button variant="outline" onClick={() => onOpenChange(false)} type="button">
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!canSubmit || Boolean(submitting)}
            onClick={() =>
              onSubmit({
                ...values,
                recruiterEmail: values.recruiterEmail.trim(),
                interviewDate: values.interviewDate.trim(),
                interviewTime: values.interviewTime.trim(),
                timezone: values.timezone.trim(),
                meetUrl: values.meetUrl.trim(),
                officeAddress: values.officeAddress.trim(),
                interviewerName: values.interviewerName.trim(),
                interviewerEmail: values.interviewerEmail.trim(),
                additionalNotes: values.additionalNotes.trim(),
              })
            }
          >
            {submitting ? "Sending invite..." : "Send invite"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
