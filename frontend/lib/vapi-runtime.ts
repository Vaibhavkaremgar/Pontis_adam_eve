export type VapiRuntimeSnapshot = {
  runtime: string;
  currentOrigin: string;
  currentHostname: string;
  currentHref: string;
  configuredPublicAppUrl: string;
  effectivePublicOrigin: string;
  isLocalDevelopment: boolean;
  isNgrokOrigin: boolean;
  originMatchesConfiguredUrl: boolean;
};

function readPublicAppUrl(): string {
  return process.env.NEXT_PUBLIC_PUBLIC_APP_URL?.trim() || "";
}

export function getCurrentOrigin(): string {
  if (typeof window === "undefined") return "";
  return window.location.origin;
}

export function getConfiguredPublicAppUrl(): string {
  return readPublicAppUrl();
}

export function getEffectiveVapiOrigin(): string {
  const configured = readPublicAppUrl();
  if (configured) return configured;
  return getCurrentOrigin();
}

export function getVapiRuntimeSnapshot(): VapiRuntimeSnapshot {
  const currentOrigin = getCurrentOrigin();
  const configuredPublicAppUrl = readPublicAppUrl();
  const effectivePublicOrigin = configuredPublicAppUrl || currentOrigin;
  const lowerCurrent = currentOrigin.toLowerCase();
  return {
    runtime: process.env.NODE_ENV || "unknown",
    currentOrigin,
    currentHostname: typeof window === "undefined" ? "" : window.location.hostname,
    currentHref: typeof window === "undefined" ? "" : window.location.href,
    configuredPublicAppUrl,
    effectivePublicOrigin,
    isLocalDevelopment: lowerCurrent.includes("localhost") || lowerCurrent.includes("127.0.0.1"),
    isNgrokOrigin: lowerCurrent.includes("ngrok"),
    originMatchesConfiguredUrl: configuredPublicAppUrl ? configuredPublicAppUrl === currentOrigin : true,
  };
}

export function suggestVapiOriginHint(snapshot: VapiRuntimeSnapshot): string | null {
  if (!snapshot.isLocalDevelopment && !snapshot.isNgrokOrigin) return null;
  if (snapshot.configuredPublicAppUrl && snapshot.originMatchesConfiguredUrl) return null;
  return "If Vapi still blocks localhost, add the exact current origin to the Vapi allowlist or test through the configured ngrok/public app URL.";
}
