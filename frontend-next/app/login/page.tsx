export default function LoginPage() {
  return (
    <div className="relative min-h-screen bg-[#f7f7f5] text-[#111111] font-plex">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.12),_transparent_55%),radial-gradient(circle_at_20%_35%,_rgba(17,17,17,0.04),_transparent_50%)]"
      />
      <header className="border-b border-[#e6e6e1] bg-[#f7f7f5]/70 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4 text-sm">
          <a className="flex items-center gap-2 font-display text-base font-semibold" href="/">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#e6e6e1] bg-white shadow-sm">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                className="text-[#3b82f6]"
              >
                <rect x="3" y="5" width="18" height="16" rx="3" />
                <path d="M8 3v4M16 3v4M3 10h18" strokeLinecap="round" />
              </svg>
            </span>
            AI Calendar
          </a>
          <a className="text-[13px] text-[#6b7280] transition hover:text-[#111111]" href="/">
            Back to home
          </a>
        </div>
      </header>

      <main className="mx-auto flex min-h-[calc(100vh-76px)] max-w-5xl items-center px-6 py-16">
        <div className="w-full rounded-3xl border border-[#e6e6e1] bg-white p-8 shadow-[0_30px_60px_-45px_rgba(15,23,42,0.35)] md:p-12">
          <p className="text-xs uppercase tracking-[0.2em] text-[#9ca3af]">
            Sign in
          </p>
          <h1 className="mt-4 font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            Continue with Google.
          </h1>
          <p className="mt-4 max-w-xl text-sm leading-relaxed text-[#6b7280]">
            AI Calendar only supports Google sign-in to connect your schedule
            safely. We never access data without your permission.
          </p>
          <div className="mt-8 flex flex-col gap-4 sm:flex-row sm:items-center">
            <a
              href="/auth/google/login"
              className="inline-flex items-center justify-center rounded-full border border-[#e6e6e1] bg-[#111111] px-6 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-[#1f2937]"
            >
              Sign in with Google
            </a>
            <span className="text-xs text-[#9ca3af]">
              No other login methods available.
            </span>
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-3 text-xs text-[#9ca3af]">
            <span>Admin access:</span>
            <a
              href="/admin"
              className="rounded-full border border-[#e6e6e1] bg-white px-4 py-2 text-xs font-medium text-[#111111] shadow-sm transition hover:-translate-y-[1px]"
            >
              Enter admin
            </a>
          </div>
        </div>
      </main>
    </div>
  );
}
