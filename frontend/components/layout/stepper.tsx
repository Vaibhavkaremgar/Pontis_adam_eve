/**
 * What this file does:
 * Displays progress across Company, Job, Voice, Review, Outreach, and Ready steps.
 */
import { cn } from "@/lib/utils";

const STEPS = [
  { id: 1, label: "Company" },
  { id: 2, label: "Job" },
  { id: 3, label: "Voice" },
  { id: 4, label: "Review" },
  { id: 5, label: "Outreach" },
  { id: 6, label: "Ready" },
];

type StepperProps = {
  activeStep: number;
};

export function Stepper({ activeStep }: StepperProps) {
  return (
    <div className="border-b border-[rgba(120,100,80,0.08)] bg-[#EFE6D8]/95 backdrop-blur">
      <div className="mx-auto max-w-2xl px-4 py-4">
        <div className="relative grid grid-cols-6 gap-2 text-center">
          <div className="absolute left-[8%] right-[8%] top-3 h-px bg-gray-300" />
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
