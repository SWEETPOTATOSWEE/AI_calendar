export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const GET = async () =>
  new Response("Admin mode is disabled.", { status: 410 });
