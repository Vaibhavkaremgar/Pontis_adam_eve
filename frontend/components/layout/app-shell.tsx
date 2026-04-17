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
import type { ReactNode } from "react";

import { Navbar } from "@/components/layout/navbar";
import { Stepper } from "@/components/layout/stepper";

type AppShellProps = {
  activeStep: number;
  children: ReactNode;
};

export function AppShell({ activeStep, children }: AppShellProps) {
  return (
    <div className="min-h-screen bg-background">
      <Navbar />
      <Stepper activeStep={activeStep} />
      <main className="mx-auto w-full max-w-2xl px-4 py-10">{children}</main>
    </div>
  );
}
