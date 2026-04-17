/**
 * What this file does:
 * Displays progress across Company, Job, Voice, Outreach, and Ready steps.
 *
 * What API it connects to:
 * No direct API calls here.
 *
 * How it fits in the pipeline:
 * Mirrors the orchestration stages that trigger backend APIs in each page.
 */
import { cn } from "@/lib/utils";

const STEPS = [
  { id: 1, label: "Company" },
  { id: 2, label: "Job" },
  { id: 3, label: "Voice" },
  { id: 4, label: "Outreach" },
  { id: 5, label: "Ready" }
];

type StepperProps = {
  activeStep: number;
};

export function Stepper({ activeStep }: StepperProps) {
  return (
    <div className="border-b border-[#E5E7EB] bg-[#F8F5F0]/95 backdrop-blur">
      <div className="mx-auto max-w-2xl px-4 py-4">
        <div className="relative grid grid-cols-5 gap-2 text-center">
          <div className="absolute left-[10%] right-[10%] top-3 h-px bg-gray-300" />
          {STEPS.map((step) => {
            const isActive = step.id === activeStep;
            const isDone = step.id < activeStep;
            return (
              <div key={step.id} className="relative space-y-2">
                <div
                  className={cn(
                    "mx-auto flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold",
                    isActive && "bg-amber-500 text-white",
                    isDone && "bg-[#14532D] text-white",
                    !isActive && !isDone && "bg-gray-300 text-gray-500"
                  )}
                >
                  {step.id}
                </div>
                <p
                  className={cn(
                    "text-xs",
                    isActive || isDone ? "font-semibold text-gray-900" : "text-gray-500"
                  )}
                >
                  {step.label}
                </p>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
