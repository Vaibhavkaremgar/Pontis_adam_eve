"use client";

import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Textarea } from "@/components/ui/textarea";

export type SecondRoundSchedulingValues = {
  roundType: "Second Round" | "Final Round";
  mode: "Online" | "In-Person";
  meetUrl: string;
  officeAddress: string;
  interviewerName: string;
  interviewerEmail: string;
  recruiterEmail: string;
  slots: string[];
  notes: string;
  timezone: string;
  duration: string;
  panelInterviewers: string[];
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  candidateName: string;
  role: string;
  company: string;
  defaultRecruiterEmail: string;
  submitting?: boolean;
  onSubmit: (values: SecondRoundSchedulingValues) => void;
};

const DEFAULT_VALUES: SecondRoundSchedulingValues = {
  roundType: "Second Round",
  mode: "Online",
  meetUrl: "",
  officeAddress: "",
  interviewerName: "",
  interviewerEmail: "",
  recruiterEmail: "",
  slots: [],
  notes: "",
  timezone: "",
  duration: "",
  panelInterviewers: [],
};

function splitList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

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
  const [values, setValues] = useState<SecondRoundSchedulingValues>(DEFAULT_VALUES);
  const [slotsText, setSlotsText] = useState("");
  const [panelText, setPanelText] = useState("");

  useEffect(() => {
    if (!open) return;
    setValues((current) => ({
      ...DEFAULT_VALUES,
      recruiterEmail: current.recruiterEmail || defaultRecruiterEmail || "",
      interviewerName: current.interviewerName,
      interviewerEmail: current.interviewerEmail,
    }));
    setSlotsText("");
    setPanelText("");
  }, [defaultRecruiterEmail, open]);

  const isOnline = values.mode === "Online";
  const canSubmit = useMemo(() => {
    const recruiterEmail = values.recruiterEmail.trim();
    const interviewerName = values.interviewerName.trim();
    const interviewerEmail = values.interviewerEmail.trim();
    const slots = splitList(slotsText);
    const meetUrl = values.meetUrl.trim();
    const officeAddress = values.officeAddress.trim();
    if (!recruiterEmail || !interviewerName || !interviewerEmail || slots.length === 0) return false;
    if (isOnline) return Boolean(meetUrl);
    return Boolean(officeAddress);
  }, [isOnline, slotsText, values.interviewerEmail, values.interviewerName, values.meetUrl, values.officeAddress, values.recruiterEmail]);

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Schedule the next round"
      description={`Configure the recruiter handoff for ${candidateName || "this candidate"} at ${company || "the company"}.`}
      className="max-w-3xl bg-white"
    >
      <div className="space-y-5">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Interview Round</span>
            <select
              className="h-12 w-full rounded-xl border border-slate-200 bg-white px-4 text-sm outline-none focus:ring-2 focus:ring-sky-500/20"
              value={values.roundType}
              onChange={(event) => setValues((current) => ({ ...current, roundType: event.target.value as SecondRoundSchedulingValues["roundType"] }))}
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
              onChange={(event) => setValues((current) => ({ ...current, mode: event.target.value as SecondRoundSchedulingValues["mode"] }))}
            >
              <option value="Online">Online</option>
              <option value="In-Person">In-Person</option>
            </select>
          </label>
        </div>

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
          <span className="font-medium text-slate-700">Recruiter Email</span>
          <Input
            value={values.recruiterEmail}
            onChange={(event) => setValues((current) => ({ ...current, recruiterEmail: event.target.value }))}
            placeholder="recruiter@company.com"
          />
        </label>

        {isOnline ? (
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Meet URL</span>
            <Input
              value={values.meetUrl}
              onChange={(event) => setValues((current) => ({ ...current, meetUrl: event.target.value }))}
              placeholder="https://meet.google.com/..."
            />
          </label>
        ) : (
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Office Address</span>
            <Textarea
              value={values.officeAddress}
              onChange={(event) => setValues((current) => ({ ...current, officeAddress: event.target.value }))}
              placeholder="123 Market Street, San Francisco, CA"
              className="min-h-[96px]"
            />
          </label>
        )}

        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Available Slots</span>
            <Textarea
              value={slotsText}
              onChange={(event) => setSlotsText(event.target.value)}
              placeholder="Tue 10:00 AM PT, Tue 2:00 PM PT, Wed 11:30 AM PT"
              className="min-h-[96px]"
            />
          </label>
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Panel Interviewers</span>
            <Textarea
              value={panelText}
              onChange={(event) => setPanelText(event.target.value)}
              placeholder="Priya Nair <priya@company.com>, Sam Lee <sam@company.com>"
              className="min-h-[96px]"
            />
          </label>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Timezone</span>
            <Input
              value={values.timezone}
              onChange={(event) => setValues((current) => ({ ...current, timezone: event.target.value }))}
              placeholder="America/Los_Angeles"
            />
          </label>
          <label className="space-y-2 text-sm">
            <span className="font-medium text-slate-700">Duration</span>
            <Input
              value={values.duration}
              onChange={(event) => setValues((current) => ({ ...current, duration: event.target.value }))}
              placeholder="45 minutes"
            />
          </label>
        </div>

        <label className="space-y-2 text-sm">
          <span className="font-medium text-slate-700">Notes / Instructions</span>
          <Textarea
            value={values.notes}
            onChange={(event) => setValues((current) => ({ ...current, notes: event.target.value }))}
            placeholder="Share interview focus areas, prep expectations, and any handoff notes."
            className="min-h-[120px]"
          />
        </label>

        <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
          <p className="font-medium text-slate-900">Operational summary</p>
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            <p>Candidate: {candidateName || "Unknown"}</p>
            <p>Role: {role || "Unknown"}</p>
            <p>Company: {company || "Unknown"}</p>
            <p>Mode: {values.mode}</p>
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
                slots: splitList(slotsText),
                panelInterviewers: splitList(panelText),
                recruiterEmail: values.recruiterEmail.trim(),
                interviewerEmail: values.interviewerEmail.trim(),
                interviewerName: values.interviewerName.trim(),
                meetUrl: values.meetUrl.trim(),
                officeAddress: values.officeAddress.trim(),
                notes: values.notes.trim(),
                timezone: values.timezone.trim(),
                duration: values.duration.trim(),
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

