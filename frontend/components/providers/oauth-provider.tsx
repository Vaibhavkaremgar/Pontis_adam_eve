"use client";

import { GoogleOAuthProvider } from "@react-oauth/google";

export function OAuthProvider({ children }: { children: React.ReactNode }) {
  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";
  if (!clientId) {
    console.warn("Google OAuth provider not mounted: NEXT_PUBLIC_GOOGLE_CLIENT_ID is missing.");
    return <>{children}</>;
  }
  return (
    <GoogleOAuthProvider
      clientId={clientId}
      onScriptLoadSuccess={() => {
        console.log("Google GSI script loaded successfully");
      }}
      onScriptLoadError={() => {
        console.error("Google GSI script failed to load");
      }}
    >
      {children}
    </GoogleOAuthProvider>
  );
}
