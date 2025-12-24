import Script from "next/script";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const bodyTemplate = readFileSync(
  join(process.cwd(), "templates", "calendar-body.html"),
  "utf8"
);

type AppContext = {
  admin: boolean;
  google_linked: boolean;
  mode: string;
};

const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? "/api";

function resolveAppContext(): AppContext {
  const admin = process.env.NEXT_PUBLIC_APP_ADMIN === "1";
  const googleLinked = process.env.NEXT_PUBLIC_APP_GOOGLE_LINKED === "1";
  const modeFromEnv = process.env.NEXT_PUBLIC_APP_MODE;
  const mode = admin ? "admin" : modeFromEnv ?? (googleLinked ? "google" : "local");
  return {
    admin,
    google_linked: googleLinked,
    mode,
  };
}

const appContext = resolveAppContext();
const bodyHtml = bodyTemplate;
const contextScript = `window.__API_BASE__ = ${JSON.stringify(apiBase)}; window.__APP_CONTEXT__ = ${JSON.stringify(appContext)};`;

export default function CalendarPage() {
  return (
    <main className="min-h-screen">
      <Script
        id="app-context"
        strategy="beforeInteractive"
        dangerouslySetInnerHTML={{ __html: contextScript }}
      />
      <Script
        src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.js"
        strategy="beforeInteractive"
      />
      <Script src="/calendar-app.js" strategy="afterInteractive" />
      <div dangerouslySetInnerHTML={{ __html: bodyHtml }} />
    </main>
  );
}
