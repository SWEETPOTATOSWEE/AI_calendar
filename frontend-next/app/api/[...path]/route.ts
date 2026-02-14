import { NextRequest } from "next/server";

import { proxyToBackend } from "../../_lib/backend-proxy";

type RouteContext = {
  params: Promise<{ path?: string[] }> | { path?: string[] };
};

export const runtime = "nodejs";
export const dynamic = "force-static";

export const generateStaticParams = () => {
  return [{ path: ['_'] }];
};

const handle = async () => {
  return new Response("This route is handled by the backend.", { status: 200 });
};

export const GET = handle;
export const HEAD = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
