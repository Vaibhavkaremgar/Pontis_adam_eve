/**
 * What this file does:
 * Wraps each intake page with a shared navbar, stepper, and centered content area.
 *
 * What API it connects to:
 * No direct API calls here.
 *
 * How it fits in the pipeline:
 * Provides consistent shell for each step that performs backend-connected actions.
 */
import { Suspense, type ReactNode } from "react";

import { Navbar } from "@/components/layout/navbar";
import { Stepper } from "@/components/layout/stepper";

type AppShellProps = {
  activeStep: number;
  children: ReactNode;
  contentClassName?: string;
};

export function AppShell({ activeStep, children, contentClassName }: AppShellProps) {
  return (
    <div className="min-h-screen bg-background">
      <Suspense fallback={<div className="h-[64px] border-b border-[rgba(120,100,80,0.08)] bg-[#EDE5D8]" />}>
        <Navbar />
      </Suspense>
      <Stepper activeStep={activeStep} />
      <main className={contentClassName || "mx-auto w-full max-w-4xl px-4 py-10"}>{children}</main>
    </div>
  );
}
