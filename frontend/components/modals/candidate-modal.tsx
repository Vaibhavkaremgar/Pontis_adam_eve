"use client";

/**
 * What this file does:
 * Shows candidate search results and actions after backend candidate fetch completes.
 *
 * What API it connects to:
 * Reads data returned by GET /candidates (fetched in parent pages via /lib/api/candidates).
 *
 * How it fits in the pipeline:
 * Presents ranked matches and moves recruiter to voice intake for refinement.
 */
import Link from "next/link";
import { useState } from "react";

import { useAppContext } from "@/context/AppContext";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Modal } from "@/components/ui/modal";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

type CandidateModalProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function CandidateModal({ open, onOpenChange }: CandidateModalProps) {
  const { candidates, isRefined } = useAppContext();
  const [bookCallMessage, setBookCallMessage] = useState("");

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Top Candidate Matches"
      description="Ranked shortlist for this role"
    >
      <div className="space-y-5">
        {isRefined && (
          <div className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
            Refined based on your input
          </div>
        )}

        <div className="max-h-[52vh] space-y-3 overflow-y-auto pr-1">
          {candidates.slice(0, 10).map((candidate) => (
            <Card key={candidate.id} className="rounded-xl p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-1">
                  <h4 className="font-semibold text-gray-900">{candidate.name}</h4>
                  <p className="text-sm text-gray-600">{candidate.role}</p>
                </div>
                <Badge
                  variant={
                    candidate.strategy === "HIGH"
                      ? "high"
                      : candidate.strategy === "MEDIUM"
                        ? "medium"
                        : "low"
                  }
                >
                  {candidate.fitScore}% score
                </Badge>
              </div>
              <Separator className="my-3" />
              <p className="text-sm text-gray-600">{candidate.summary}</p>
            </Card>
          ))}
          {candidates.length === 0 && (
            <Card className="rounded-xl p-4 text-sm text-gray-600">
              No candidates yet. Submit the job brief first.
            </Card>
          )}
        </div>

        {bookCallMessage && <p className="text-sm text-gray-700">{bookCallMessage}</p>}

        <div className="grid gap-2 sm:grid-cols-2">
          <Button
            variant="outline"
            className="justify-center"
            onClick={() => setBookCallMessage("Book a Call flow will connect to calendar tooling in backend.")}
          >
            Book a Call
          </Button>
          <Link
            href="/voice"
            onClick={() => onOpenChange(false)}
            className={cn(buttonVariants({ variant: "default" }), "justify-center")}
          >
            Continue to Voice Intake
          </Link>
        </div>
      </div>
    </Modal>
  );
}
