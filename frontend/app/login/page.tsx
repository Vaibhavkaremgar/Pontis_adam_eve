"use client";

/**
 * What this file does:
 * Handles recruiter login with strict email auth and Google placeholder behavior.
 *
 * What API it connects to:
 * Uses /lib/api/auth -> POST /auth/login via centralized API client.
 *
 * How it fits in the pipeline:
 * This is the required entry gate before recruiter can access company/job/candidate pipeline.
 * Auth data is stored in a standard DB (not vector DB).
 */
import Image from "next/image";
import { GoogleLogin, GoogleOAuthProvider } from "@react-oauth/google";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAppContext } from "@/context/AppContext";
import { login, loginWithGoogle } from "@/lib/api/auth";
import { cn } from "@/lib/utils";

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function LoginPage() {
  const router = useRouter();
  const { setUser, setToken } = useAppContext();

  const [email, setEmail] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [isGoogleLoading, setIsGoogleLoading] = useState(false);
  const googleClientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";
  const isGoogleConfigured = Boolean(googleClientId.trim());

  const emailTrimmed = email.trim();
  const hasValidEmail = EMAIL_REGEX.test(emailTrimmed);
  const canContinue = hasValidEmail && !isLoading;

  const handleEmailLogin = async () => {
    if (!emailTrimmed) return;

    if (!EMAIL_REGEX.test(emailTrimmed)) {
      setError("Please enter a valid email address.");
      return;
    }

    // This handles real-world API delays and failures.
    try {
      setIsLoading(true);
      setError("");

      const result = await login({ email: emailTrimmed, provider: "email" });
      if (!result.success || !result.data) {
        setError(result.error || "Login failed. Please try again.");
        return;
      }

      setToken(result.data.access_token || result.data.token);
      setUser(result.data.user);
      router.push("/company");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#F8F5F0] px-4 py-10">
      <div className="w-full max-w-xl space-y-6 text-center">
        <div className="mx-auto flex h-24 w-24 items-center justify-center rounded-full border border-[#E5E7EB] bg-white text-xl font-semibold text-gray-900 shadow-sm">
          <Image
            src="/images/Maya.jpg.jpeg"
            alt="Maya avatar"
            width={96}
            height={96}
            className="h-full w-full rounded-full object-cover"
            priority
          />
        </div>

        <div className="space-y-2">
          <h1 className="text-3xl font-semibold text-gray-900">Meet Maya</h1>
          <p className="text-sm text-gray-600">Land the perfect hire for your team</p>
        </div>

        <Card className="mx-auto w-full max-w-[560px] text-left">
          <CardHeader className="space-y-2 text-center">
            <CardTitle className="text-2xl">Welcome</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            {isGoogleConfigured ? (
              <div className={cn(buttonVariants({ variant: "outline" }), "w-full justify-center p-0")}>
                <GoogleOAuthProvider clientId={googleClientId}>
                  <GoogleLogin
                    onSuccess={async (credentialResponse) => {
                      const idToken = credentialResponse.credential;
                      if (!idToken) {
                        setError("Google login failed: missing credential.");
                        return;
                      }
                      setIsGoogleLoading(true);
                      setError("");
                      const result = await loginWithGoogle({ token: idToken });
                      if (!result.success || !result.data) {
                        setError(result.error || "Google login failed. Please try again.");
                        setIsGoogleLoading(false);
                        return;
                      }
                      setToken(result.data.access_token || result.data.token);
                      setUser(result.data.user);
                      setIsGoogleLoading(false);
                      router.push("/company");
                    }}
                    onError={() => setError("Login failed. Please try again.")}
                  />
                </GoogleOAuthProvider>
              </div>
            ) : (
              <button
                type="button"
                disabled
                className={cn(buttonVariants({ variant: "outline" }), "w-full justify-center gap-2 opacity-70")}
              >
                <Image src="/images/google-g-logo.svg" alt="Google logo" width={18} height={18} />
                Google sign-in unavailable
              </button>
            )}
            {isGoogleLoading && <p className="text-sm text-gray-600">Signing in with Google...</p>}
            {!isGoogleConfigured && (
              <p className="text-sm text-gray-600">Set NEXT_PUBLIC_GOOGLE_CLIENT_ID in frontend/.env.local to enable Google login.</p>
            )}

            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-[#E5E7EB]" />
              <span className="text-[11px] font-semibold tracking-[0.08em] text-gray-500">
                OR CONTINUE WITH WORK EMAIL
              </span>
              <div className="h-px flex-1 bg-[#E5E7EB]" />
            </div>

            <div className="space-y-3">
              <Input
                type="email"
                placeholder="Work email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
              {!hasValidEmail && emailTrimmed.length > 0 && (
                <p className="text-sm text-red-600">Please enter a valid email address.</p>
              )}
              <Button className="w-full justify-center" onClick={handleEmailLogin} disabled={!canContinue}>
                {isLoading ? "Loading..." : "Continue with email"}
              </Button>
            </div>

            {error && <p className="text-sm text-red-600">{error}</p>}
          </CardContent>
        </Card>

        <p className="text-xs text-gray-500">Pontis.one - All Rights Reserved</p>
      </div>
    </main>
  );
}
