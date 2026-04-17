"use client";

/**
 * What this file does:
 * Displays loading feedback while candidate evaluation is running.
 *
 * What API it connects to:
 * Used around POST /api/hiring/create and GET /api/candidates async calls in parent pages.
 *
 * How it fits in the pipeline:
 * Keeps recruiter informed during the 2-3 second evaluation window before candidate results appear.
 */
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Progress } from "@/components/ui/progress";

type LoadingModalProps = {
  open: boolean;
  progress: number;
};

export function LoadingModal({ open, progress }: LoadingModalProps) {
  return (
    <Modal
      open={open}
      onOpenChange={() => {
        // This modal stays controlled by parent async state to avoid accidental close.
      }}
      title="Evaluating candidates..."
      description="Analyzing fit and outreach strategy"
      hideClose
    >
      <div className="space-y-5">
        <Progress value={progress} />
        <div className="flex flex-wrap gap-2">
          <Badge variant="high">HIGH</Badge>
          <Badge variant="medium">MEDIUM</Badge>
          <Badge variant="low">LOW</Badge>
        </div>
        <p className="text-sm text-gray-600">
          Matching role requirements with candidate intent and hiring outcomes.
        </p>
      </div>
    </Modal>
  );
}
