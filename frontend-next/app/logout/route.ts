import { NextRequest } from "next/server";

import { proxyToBackend } from "../_lib/backend-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const GET = async (req: NextRequest) => proxyToBackend(req, "/logout");
