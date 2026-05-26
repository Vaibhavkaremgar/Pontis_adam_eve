/**
 * What this file does:
 * Displays progress across the recruiter flow: Company, Job, Voice, Review, Ready, and Results.
 */
import { cn } from "@/lib/utils";

const STEPS = [
  { id: 1, label: "Company" },
  { id: 2, label: "Job" },
  { id: 3, label: "Voice" },
  { id: 4, label: "Review" },
  { id: 5, label: "Ready" },
  { id: 6, label: "Results" },
];

type StepperProps = {
  activeStep: number;
};

export function Stepper({ activeStep }: StepperProps) {
  return (
    <div className="border-b border-[rgba(120,100,80,0.08)] bg-[#EFE6D8]/95 backdrop-blur">
      <div className="mx-auto w-full max-w-6xl px-4 py-4">
        <div className="relative grid grid-cols-6 gap-1.5 text-center sm:gap-2">
          <div className="absolute left-[4%] right-[4%] top-3 h-px bg-gray-300" />
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
