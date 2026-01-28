import { NextRequest, NextResponse } from "next/server";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const BACKEND_BASE_URL = (process.env.BACKEND_INTERNAL_URL || process.env.BACKEND_BASE_URL || DEFAULT_BACKEND_URL).replace(/\/$/, "");
const BACKEND_ORIGIN = new URL(BACKEND_BASE_URL).origin;

const withTrailingSlash = (path: string) => (path.startsWith("/") ? path : `/${path}`);

const safeHeader = (headers: Headers, name: string, value: string | null) => {
  if (value) {
    headers.set(name, value);
  }
};

const collectSetCookies = (res: Response): string[] => {
  const getSetCookie = (
    res.headers as unknown as { getSetCookie?: () => string[] }
  ).getSetCookie?.bind(res.headers);
  if (getSetCookie) {
    return getSetCookie();
  }
  const raw = res.headers.get("set-cookie");
  return raw ? [raw] : [];
};

const rewriteLocation = (location: string, req: NextRequest) => {
  try {
    const target = new URL(location, req.nextUrl.origin);
    if (target.origin === BACKEND_ORIGIN) {
      return `${req.nextUrl.origin}${target.pathname}${target.search}${target.hash}`;
    }
  } catch {
    // Ignore malformed URLs and fall back to original location
  }
  return location;
};

export const proxyToBackend = async (req: NextRequest, backendPath: string) => {
  const backendUrl = new URL(withTrailingSlash(backendPath), BACKEND_BASE_URL);
  backendUrl.search = req.nextUrl.search;

  const headers = new Headers();
  safeHeader(headers, "accept", req.headers.get("accept"));
  safeHeader(headers, "accept-language", req.headers.get("accept-language"));
  safeHeader(headers, "authorization", req.headers.get("authorization"));
  safeHeader(headers, "content-type", req.headers.get("content-type"));
  safeHeader(headers, "cookie", req.headers.get("cookie"));
  safeHeader(headers, "user-agent", req.headers.get("user-agent"));
  safeHeader(headers, "x-forwarded-host", req.headers.get("host"));
  safeHeader(headers, "x-forwarded-proto", req.nextUrl.protocol.replace(":", ""));

  const init: RequestInit & { duplex?: "half" } = {
    method: req.method,
    headers,
    redirect: "manual",
  };

  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = req.body;
    init.duplex = "half";
  }

  const backendResponse = await fetch(backendUrl, init);

  const responseHeaders = new Headers();
  safeHeader(responseHeaders, "content-type", backendResponse.headers.get("content-type"));
  safeHeader(responseHeaders, "cache-control", backendResponse.headers.get("cache-control"));
  safeHeader(responseHeaders, "pragma", backendResponse.headers.get("pragma"));

  const location = backendResponse.headers.get("location");
  if (location) {
    responseHeaders.set("location", rewriteLocation(location, req));
  }

  for (const cookie of collectSetCookies(backendResponse)) {
    responseHeaders.append("set-cookie", cookie);
  }

  const body = req.method === "HEAD" ? null : backendResponse.body;
  return new NextResponse(body, {
    status: backendResponse.status,
    headers: responseHeaders,
  });
};
