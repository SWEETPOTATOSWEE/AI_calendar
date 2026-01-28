import { NextRequest } from "next/server";

import { proxyToBackend } from "../../_lib/backend-proxy";

type RouteContext = {
  params: Promise<{ path?: string[] }> | { path?: string[] };
};

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const handle = async (req: NextRequest, context: RouteContext) => {
  const params = await context.params;
  const segments = params?.path ?? [];
  const backendPath = `/api/${segments.join("/")}`;
  return proxyToBackend(req, backendPath);
};

export const GET = handle;
export const HEAD = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
