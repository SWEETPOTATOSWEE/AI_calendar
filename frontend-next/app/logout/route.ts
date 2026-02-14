import { NextRequest } from "next/server";

import { proxyToBackend } from "../_lib/backend-proxy";

export const runtime = "nodejs";
export const dynamic = "force-static";

export const GET = async () => 
  new Response("This route is handled by the backend.", { status: 200 });
