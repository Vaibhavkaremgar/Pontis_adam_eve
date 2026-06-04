import { NextResponse } from "next/server";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade"
]);

const STRIP_RESPONSE_HEADERS = new Set(["content-encoding", "content-length"]);

function getBackendBaseUrl() {
  const url = process.env.BACKEND_API_URL?.trim();
  if (!url) {
    throw new Error("BACKEND_API_URL is required");
  }
  return url.replace(/\/$/, "");
}

async function proxyRequest(request: Request, pathParts: string[]) {
  const backendUrl = getBackendBaseUrl();
  const incomingUrl = new URL(request.url);
  const targetUrl = new URL(`${backendUrl}/${pathParts.join("/")}`);
  targetUrl.search = incomingUrl.search;

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");
  for (const header of HOP_BY_HOP_HEADERS) {
    headers.delete(header);
  }

  const init: RequestInit = {
    method: request.method,
    headers,
    redirect: "manual"
  };

  if (!["GET", "HEAD"].includes(request.method)) {
    init.body = await request.arrayBuffer();
  }

  const response = await fetch(targetUrl, init);
  const responseHeaders = new Headers();
  response.headers.forEach((value, name) => {
    const lower = name.toLowerCase();
    if (HOP_BY_HOP_HEADERS.has(lower) || STRIP_RESPONSE_HEADERS.has(lower) || lower === "set-cookie") {
      return;
    }
    responseHeaders.append(name, value);
  });

  const getSetCookie = (response.headers as Headers & { getSetCookie?: () => string[] }).getSetCookie;
  const setCookieHeaders =
    typeof getSetCookie === "function"
      ? getSetCookie.call(response.headers)
      : response.headers
          .get("set-cookie")
          ?.split(/,(?=[^;]+=[^;]+)/g)
          .map((cookie) => cookie.trim())
          .filter(Boolean) ?? [];

  const proxiedResponse = new NextResponse(await response.arrayBuffer(), {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders
  });

  for (const cookie of setCookieHeaders) {
    proxiedResponse.headers.append("set-cookie", cookie);
  }

  return proxiedResponse;
}

export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path);
}

export async function POST(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path);
}

export async function PUT(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path);
}

export async function PATCH(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path);
}

export async function DELETE(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyRequest(request, path);
}
