export const runtime = "nodejs";
export const dynamic = "force-static";

export const GET = async () =>
  new Response("Admin mode is disabled.", { status: 410 });
