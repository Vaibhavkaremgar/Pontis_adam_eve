"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { getSession, bookSession, type InterviewSession } from "@/lib/api/interviews";

function resolveBookingLink(session: InterviewSession | null): string {
  return session?.bookingLink || session?.bookingUrl || "#";
}

function openExternalLink(href: string) {
  if (!href || href === "#") return;
  window.open(href, "_blank", "noopener,noreferrer");
}

function InterviewBookingContent() {
  const searchParams = useSearchParams();
  const token = useMemo(() => searchParams.get("token") || "", [searchParams]);
  const [session, setSession] = useState<InterviewSession | null>(null);
  const [loading, setLoading] = useState(false);
  const [booking, setBooking] = useState(false);
  const [status, setStatus] = useState("");
  const [scheduledAt, setScheduledAt] = useState("");

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    getSession(token).then((result) => {
      if (result.success && result.data) {
        setSession(result.data);
        setStatus("");
      } else {
        setStatus(result.error || "Could not load interview session.");
      }
      setLoading(false);
    });
  }, [token]);

  const canBook = Boolean(session && token && !booking && !loading);
  const bookingLink = resolveBookingLink(session);
  const canOpenBookingLink = bookingLink !== "#";
  const canJoinInterview = Boolean(session?.meetingLink && session.meetingLink !== "#");

  const handleBook = async () => {
    if (!canBook) return;
    setBooking(true);
    setStatus("");
    const result = await bookSession({ token, scheduledAt: scheduledAt || null });
    if (!result.success || !result.data) {
      setStatus(result.error || "Could not book interview.");
      setBooking(false);
      return;
    }
    const bookedSession = result.data;
    setStatus("Interview booked successfully.");
    setBooking(false);
    setSession((prev) =>
      prev
        ? {
            ...prev,
            ...bookedSession,
            status: "booked",
            bookedAt: new Date().toISOString(),
            meetingLink: bookedSession.meetingLink || prev.meetingLink,
          }
        : prev
    );
  };

  return (
    <AppShell activeStep={6}>
      <Card className="mx-auto w-full max-w-xl">
        <CardHeader>
          <CardTitle>Book your interview</CardTitle>
          <CardDescription>Choose a time and confirm your slot.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {loading && <p className="text-sm text-gray-600">Loading booking details...</p>}
          {!loading && !session && token && <p className="text-sm text-red-600">{status || "Invalid or expired booking link."}</p>}
          {session && (
            <>
              <div className="space-y-2 rounded-2xl border border-[rgba(120,100,80,0.08)] bg-[#EFE6D8] p-4">
                <p className="text-sm font-medium text-gray-900">{session.email}</p>
                <p className="text-sm text-gray-600">Job: {session.jobId}</p>
                <Badge variant={session.status === "booked" ? "high" : "medium"}>{session.status}</Badge>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-900">Preferred interview time</label>
                <Input
                  type="datetime-local"
                  value={scheduledAt}
                  onChange={(e) => setScheduledAt(e.target.value)}
                  disabled={!canBook}
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Button className="justify-center" onClick={handleBook} disabled={!canBook}>
                  {booking ? "Booking..." : "Confirm Interview"}
                </Button>
                <Button
                  variant="outline"
                  className="justify-center"
                  onClick={() => openExternalLink(bookingLink)}
                  disabled={!canOpenBookingLink}
                >
                  Book Interview
                </Button>
              </div>
              {session.status === "booked" && session.meetingLink && (
                <Button
                  className="w-full justify-center"
                  onClick={() => openExternalLink(session.meetingLink || "#")}
                  disabled={!canJoinInterview}
                >
                  Join Interview
                </Button>
              )}
            </>
          )}
          {status && <p className="text-sm text-gray-700">{status}</p>}
        </CardContent>
      </Card>
    </AppShell>
  );
}

export default function InterviewBookingPage() {
  return (
    <Suspense fallback={<div className="mx-auto w-full max-w-xl p-6 text-sm text-gray-600">Loading booking page...</div>}>
      <InterviewBookingContent />
    </Suspense>
  );
}
