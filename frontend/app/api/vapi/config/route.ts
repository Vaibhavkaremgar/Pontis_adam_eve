import { NextResponse } from "next/server";

export async function GET() {
  const publicKey = process.env.NEXT_PUBLIC_VAPI_PUBLIC_KEY?.trim() || "";
  const assistantId = process.env.NEXT_PUBLIC_VAPI_ASSISTANT_ID?.trim() || "";
  const publicAppUrl = process.env.NEXT_PUBLIC_PUBLIC_APP_URL?.trim() || "";

  return NextResponse.json({
    success: true,
    data: {
      publicKey,
      assistantId,
      publicAppUrl,
      hasPublicKey: Boolean(publicKey),
      hasAssistantId: Boolean(assistantId),
      hasPublicAppUrl: Boolean(publicAppUrl),
    }
  });
}
