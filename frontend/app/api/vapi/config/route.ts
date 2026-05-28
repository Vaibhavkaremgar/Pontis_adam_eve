import { NextResponse } from "next/server";

export async function GET() {
  const publicKey =
    process.env.NEXT_PUBLIC_VAPI_PUBLIC_KEY?.trim() ||
    process.env.VAPI_PUBLIC_KEY?.trim() ||
    "";
  const assistantId =
    process.env.NEXT_PUBLIC_VAPI_ASSISTANT_ID?.trim() ||
    process.env.VAPI_ASSISTANT_ID?.trim() ||
    "";
  const publicAppUrl =
    process.env.NEXT_PUBLIC_PUBLIC_APP_URL?.trim() ||
    process.env.PUBLIC_APP_URL?.trim() ||
    process.env.APP_URL?.trim() ||
    "";

  const hasPublicKey = Boolean(publicKey);
  const hasAssistantId = Boolean(assistantId);
  const missing: string[] = [];
  if (!hasPublicKey) missing.push("Vapi public key");
  if (!hasAssistantId) missing.push("Vapi assistant ID");

  if (missing.length > 0) {
    return NextResponse.json(
      {
        success: false,
        error: `Missing production Vapi config: ${missing.join(", ")}. Set NEXT_PUBLIC_* or VAPI_* env vars on the Railway frontend service.`,
        data: {
          publicKey,
          assistantId,
          publicAppUrl,
          hasPublicKey,
          hasAssistantId,
          hasPublicAppUrl: Boolean(publicAppUrl),
        },
      },
      { status: 500 }
    );
  }

  return NextResponse.json({
    success: true,
    data: {
      publicKey,
      assistantId,
      publicAppUrl,
      hasPublicKey,
      hasAssistantId,
      hasPublicAppUrl: Boolean(publicAppUrl),
    }
  });
}
