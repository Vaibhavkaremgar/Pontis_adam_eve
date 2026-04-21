/**
 * What this file does:
 * Root layout for the entire Next.js app.
 *
 * What API it connects to:
 * No direct API calls here.
 *
 * How it fits in the pipeline:
 * Injects AppProvider so all pages can orchestrate backend API calls with shared state.
 */
import type { Metadata } from "next";
import "@fontsource/inter/index.css";
import "@fontsource/playfair-display/600.css";
import "@fontsource/playfair-display/700.css";

import { OAuthProvider } from "@/components/providers/oauth-provider";
import { AppProvider } from "@/context/AppContext";

import "./globals.css";

export const metadata: Metadata = {
  title: "Pontis",
  description: "Hiring intake and candidate matching platform"
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans">
        <OAuthProvider>
          <AppProvider>{children}</AppProvider>
        </OAuthProvider>
      </body>
    </html>
  );
}
