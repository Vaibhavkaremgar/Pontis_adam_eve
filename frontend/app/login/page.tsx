"use client";

/**
 * What this file does:
 * Handles recruiter login via OTP email verification and Google OAuth.
 *
 * What API it connects to:
 * POST /auth/request-otp, POST /auth/verify-otp, POST /auth/google
 *
 * How it fits in the pipeline:
 * Required entry gate before recruiter can access company/job/candidate pipeline.
 */
import Image from "next/image";
import { GoogleLogin } from "@react-oauth/google";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAppContext } from "@/context/AppContext";
import { requestOtp, verifyOtp, loginWithGoogle } from "@/lib/api/auth";
import { cn } from "@/lib/utils";

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function LoginPage() {
  const router = useRouter();
  const { setUser, setToken } = useAppContext();

  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [step, setStep] = useState<"email" | "otp">("email");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [isGoogleLoading, setIsGoogleLoading] = useState(false);

  const googleClientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";
  const isGoogleConfigured = Boolean(googleClientId.trim());

  const emailTrimmed = email.trim();
  const hasValidEmail = EMAIL_REGEX.test(emailTrimmed);

  const handleRequestOtp = async () => {
    if (!hasValidEmail) {
      setError("Please enter a valid email address.");
      return;
    }
    try {
      setIsLoading(true);
      setError("");
      const result = await requestOtp({ email: emailTrimmed });
      if (!result.success) {
        setError(result.error || "Failed to send OTP. Please try again.");
        return;
      }
      setStep("otp");
    } finally {
      setIsLoading(false);
    }
  };

  const handleVerifyOtp = async () => {
    const otpTrimmed = otp.trim();
    if (!otpTrimmed) {
      setError("Please enter the OTP sent to your email.");
      return;
    }
    try {
      setIsLoading(true);
      setError("");
      const result = await verifyOtp({ email: emailTrimmed, otp: otpTrimmed });
      if (!result.success || !result.data) {
        setError(result.error || "Invalid or expired OTP. Please try again.");
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
    <main className="flex min-h-screen items-center justify-center bg-[#F6F1E8] px-4 py-10">
      <div className="w-full max-w-xl space-y-6 text-center">
        <div className="mx-auto flex h-24 w-24 items-center justify-center rounded-full border border-[rgba(120,100,80,0.08)] bg-[#F3EDE3] text-xl font-semibold text-gray-900 shadow-[0_4px_12px_rgba(0,0,0,0.02)]">
          <Image
            src="/images/adam.png"
            alt="Maya avatar"
            width={96}
            height={96}
            className="h-full w-full rounded-full object-cover"
            priority
          />
        </div>

        <div className="space-y-2">
          <h1 className="text-3xl font-semibold text-gray-900">Meet Adam</h1>
          <p className="text-sm text-gray-600">Land the perfect hire for your team</p>
        </div>

        <Card className="mx-auto w-full max-w-[560px] text-left">
          <CardHeader className="space-y-2 text-center">
            <CardTitle className="text-2xl">Welcome</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            {isGoogleConfigured ? (
              <div className="flex w-full justify-center">
                <GoogleLogin
                  width={320}
                  theme="outline"
                  size="large"
                  shape="rectangular"
                  text="continue_with"
                  logo_alignment="left"
                  click_listener={() => {
                    console.log("Google button clicked");
                  }}
                  onSuccess={async (credentialResponse) => {
                    console.log("Google success", credentialResponse);
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
                  onError={() => {
                    console.log("Google error", "Google OAuth button failed or was dismissed");
                    setError("Login failed. Please try again.");
                  }}
                />
              </div>
            ) : (
              <button
                type="button"
                disabled
                className={cn(
                  "flex h-12 w-full items-center justify-center gap-3 rounded-xl border border-[rgba(120,100,80,0.12)] bg-[#F3EDE3] px-4 text-sm font-medium text-gray-500 opacity-70"
                )}
              >
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[#EFE6D8] ring-1 ring-[rgba(120,100,80,0.08)]">
                  <Image src="/images/google-g-logo.svg" alt="" width={16} height={16} className="h-4 w-4 opacity-90" />
                </span>
                Google sign-in unavailable
              </button>
            )}
            {isGoogleLoading && <p className="text-sm text-gray-600">Signing in with Google...</p>}
            {!isGoogleConfigured && (
              <p className="text-sm text-gray-600">
                Set NEXT_PUBLIC_GOOGLE_CLIENT_ID in frontend/.env.local to enable Google login.
              </p>
            )}

            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-[rgba(120,100,80,0.08)]" />
              <span className="text-[11px] font-semibold tracking-[0.08em] text-gray-500">
                OR CONTINUE WITH WORK EMAIL
              </span>
              <div className="h-px flex-1 bg-[rgba(120,100,80,0.08)]" />
            </div>

            {step === "email" ? (
              <div className="space-y-3">
                <Input
                  type="email"
                  placeholder="Work email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleRequestOtp()}
                />
                {!hasValidEmail && emailTrimmed.length > 0 && (
                  <p className="text-sm text-red-600">Please enter a valid email address.</p>
                )}
                <Button
                  className="w-full justify-center"
                  onClick={handleRequestOtp}
                  disabled={!hasValidEmail || isLoading}
                >
                  {isLoading ? "Sending..." : "Send OTP"}
                </Button>
              </div>
            ) : (
              <div className="space-y-3">
                <p className="text-sm text-gray-600">
                  A 6-digit code was sent to <span className="font-medium">{emailTrimmed}</span>.
                </p>
                <Input
                  type="text"
                  inputMode="numeric"
                  placeholder="Enter 6-digit OTP"
                  maxLength={6}
                  value={otp}
                  onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
                  onKeyDown={(e) => e.key === "Enter" && handleVerifyOtp()}
                />
                <Button
                  className="w-full justify-center"
                  onClick={handleVerifyOtp}
                  disabled={otp.trim().length !== 6 || isLoading}
                >
                  {isLoading ? "Verifying..." : "Verify OTP"}
                </Button>
                <button
                  type="button"
                  className="text-sm text-gray-500 underline"
                  onClick={() => { setStep("email"); setOtp(""); setError(""); }}
                >
                  Use a different email
                </button>
              </div>
            )}

            {error && <p className="text-sm text-red-600">{error}</p>}
          </CardContent>
        </Card>

        <p className="text-xs text-gray-500">Pontis.one - All Rights Reserved</p>
      </div>
    </main>
  );
}
