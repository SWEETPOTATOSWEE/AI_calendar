export default function Home() {
  const navLinks = [
    { label: "Features", href: "#features" },
    { label: "Demo", href: "#demo" },
    { label: "Security", href: "#security" },
    { label: "Pricing", href: "#cta" },
  ];

  const features = [
    {
      title: "Natural-language input",
      description:
        "Type \"dentist tomorrow 3pm\" and it becomes a scheduled event instantly.",
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
          <path d="M7 7h10M7 12h10M7 17h6" strokeLinecap="round" />
          <rect x="4" y="4" width="16" height="16" rx="4" />
        </svg>
      ),
    },
    {
      title: "Conflict detection",
      description:
        "Overlaps are flagged automatically with smart alternatives before you hit save.",
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
          <path d="M8 6h8M6 10h12M8 14h8M6 18h12" strokeLinecap="round" />
          <circle cx="18" cy="6" r="3" />
        </svg>
      ),
    },
    {
      title: "Priority recommendations",
      description:
        "Your day is ordered by importance and urgency so high-impact tasks come first.",
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
          <path d="M6 18l4-4 3 3 5-6" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M5 5h14v14H5z" />
        </svg>
      ),
    },
  ];

  const steps = [
    {
      title: "Tell it your plans",
      description: "Natural phrases, no form-filling.",
    },
    {
      title: "AI organizes everything",
      description: "Sorting, reminders, and recurring logic handled.",
    },
    {
      title: "Only the alerts you need",
      description: "Stay focused without notification overload.",
    },
  ];

  const weekDays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const dates = [12, 13, 14, 15, 16, 17, 18];

  const suggestions = [
    "Moved Thursday meeting to 10:00 AM for focus time optimization.",
    "Conflict detected Friday 2-4 PM -> propose 4:30 PM.",
    "Added 20-minute travel buffer before Dentist appt.",
  ];

  const schedule = [
    {
      time: "09:00 AM",
      title: "Design Sync",
      meta: "Product team - Zoom",
      tone: "bg-white",
    },
    {
      time: "10:00 AM",
      title: "Focus Block",
      meta: "Recommended by AI for deep work",
      tone: "bg-[#f0f6ff]",
    },
    {
      time: "01:30 PM",
      title: "Dentist Appointment",
      meta: "Dr. Smith - 123 Main St",
      tone: "bg-white",
    },
  ];

  const briefings = [
    {
      title: "Schedule Health",
      detail: "Today: 3 events - 1 focus block recommended.",
    },
    {
      title: "Smart Logistics",
      detail: "Travel time auto-added: 20 min based on traffic.",
    },
    {
      title: "Tomorrow's Lookahead",
      detail: "Light day. Suggestion: Move Friday prep to 4 PM.",
    },
  ];

  return (
    <div className="relative min-h-screen bg-[#f7f7f5] text-[#111111] font-plex">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.12),_transparent_55%),radial-gradient(circle_at_20%_35%,_rgba(17,17,17,0.04),_transparent_50%)]"
      />
      <header className="sticky top-0 z-30 border-b border-[#e6e6e1] bg-[#f7f7f5]/70 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4 text-sm">
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
          <nav className="hidden items-center gap-6 text-[13px] text-[#6b7280] md:flex">
            {navLinks.map((link) => (
              <a
                key={link.label}
                href={link.href}
                className="transition hover:text-[#111111]"
              >
                {link.label}
              </a>
            ))}
          </nav>
          <div className="flex items-center gap-3">
            <a
              href="/login"
              className="hidden text-[13px] text-[#6b7280] transition hover:text-[#111111] md:inline-flex"
            >
              Login
            </a>
            <a
              href="/calendar"
              className="rounded-full bg-[#3b82f6] px-4 py-2 text-[13px] font-medium text-white shadow-sm transition hover:bg-[#3476de]"
            >
              Start free
            </a>
          </div>
        </div>
      </header>

      <main>
        <section className="mx-auto max-w-6xl px-6 pb-24 pt-20 md:pb-28 md:pt-24">
          <div className="grid items-center gap-12 md:grid-cols-[1.05fr_0.95fr]">
            <div className="animate-fade-up">
              <p className="text-xs uppercase tracking-[0.2em] text-[#9ca3af]">
                Personal AI Calendar
              </p>
              <h1 className="mt-4 font-display text-4xl font-semibold tracking-tight text-[#111111] sm:text-5xl">
                The personal calendar that{" "}
                <span className="text-[#3b82f6]">AI organizes</span> for you.
              </h1>
              <p className="mt-6 max-w-xl text-base leading-relaxed text-[#4b5563] sm:text-lg">
                Speak naturally. Your meetings, reminders, and recurring plans get
                sorted, de-conflicted, and prioritized automatically.
              </p>
              <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
                <a
                  href="/calendar"
                  className="rounded-full bg-[#3b82f6] px-6 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-[#3476de]"
                >
                  Start free
                </a>
                <a
                  href="#demo"
                  className="rounded-full border border-[#e6e6e1] bg-white px-6 py-3 text-sm font-medium text-[#111111] shadow-sm transition hover:-translate-y-[1px]"
                >
                  View demo
                </a>
              </div>
              <p className="mt-4 text-xs text-[#9ca3af]">
                No credit card required - Set up in under 1 minute
              </p>
            </div>
            <div
              className="animate-fade-up"
              style={{ animationDelay: "120ms" }}
            >
              <div className="animate-float rounded-3xl border border-[#e6e6e1] bg-white p-6 shadow-[0_30px_60px_-40px_rgba(15,23,42,0.35)]">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs text-[#9ca3af]">May 2025</p>
                    <p className="mt-1 font-display text-base font-semibold">
                      Weekly Overview
                    </p>
                  </div>
                  <div className="flex items-center gap-2 text-[#9ca3af]">
                    <span className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-[#e6e6e1]">
                      <svg
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.6"
                      >
                        <path d="M15 6l-6 6 6 6" strokeLinecap="round" />
                      </svg>
                    </span>
                    <span className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-[#e6e6e1]">
                      <svg
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.6"
                      >
                        <path d="M9 6l6 6-6 6" strokeLinecap="round" />
                      </svg>
                    </span>
                  </div>
                </div>
                <div className="mt-5 grid grid-cols-7 gap-2 text-xs text-[#9ca3af]">
                  {weekDays.map((day) => (
                    <div key={day} className="text-center">
                      {day}
                    </div>
                  ))}
                </div>
                <div className="mt-3 grid grid-cols-7 gap-2 text-sm text-[#111111]">
                  {dates.map((date) => (
                    <div
                      key={date}
                      className={`flex h-8 items-center justify-center rounded-full ${
                        date === 15
                          ? "bg-[#3b82f6] text-white shadow-sm"
                          : "text-[#6b7280]"
                      }`}
                    >
                      {date}
                    </div>
                  ))}
                </div>
                <div className="mt-6 rounded-2xl border border-[#eef0ed] bg-[#f9fafb] p-4">
                  <div className="flex items-center gap-2 text-xs font-semibold text-[#6b7280]">
                    <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-white text-[#3b82f6]">
                      <svg
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.6"
                      >
                        <path d="M12 4v16M4 12h16" strokeLinecap="round" />
                      </svg>
                    </span>
                    AI Suggestions
                  </div>
                  <ul className="mt-3 space-y-3 text-xs text-[#4b5563]">
                    {suggestions.map((item, index) => (
                      <li key={item} className="flex items-start gap-2">
                        <span
                          className={`mt-1 h-2 w-2 rounded-full ${
                            index === 0
                              ? "bg-[#3b82f6]"
                              : index === 1
                                ? "bg-[#f97316]"
                                : "bg-[#10b981]"
                          }`}
                        />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section
          id="features"
          className="mx-auto max-w-6xl px-6 pb-20 pt-4"
        >
          <div className="text-center">
            <h2 className="font-display text-3xl font-semibold tracking-tight">
              Intelligent Features
            </h2>
            <p className="mt-3 text-sm text-[#6b7280]">
              Designed to adapt to your life, not the other way around.
            </p>
          </div>
          <div className="mt-12 grid gap-6 md:grid-cols-3">
            {features.map((feature, index) => (
              <div
                key={feature.title}
                className="animate-fade-up rounded-2xl border border-[#e6e6e1] bg-white p-6 shadow-[0_20px_40px_-35px_rgba(15,23,42,0.35)]"
                style={{ animationDelay: `${120 + index * 120}ms` }}
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-full border border-[#e6e6e1] bg-[#f7f7f5] text-[#3b82f6]">
                  {feature.icon}
                </div>
                <h3 className="mt-4 font-display text-lg font-semibold">
                  {feature.title}
                </h3>
                <p className="mt-2 text-sm text-[#6b7280]">
                  {feature.description}
                </p>
              </div>
            ))}
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-6 py-20">
          <div className="grid gap-8 text-center md:grid-cols-3 md:text-left">
            {steps.map((step, index) => (
              <div key={step.title} className="animate-fade-up" style={{ animationDelay: `${150 + index * 140}ms` }}>
                <div className="flex items-center gap-3 justify-center md:justify-start">
                  <span className="flex h-9 w-9 items-center justify-center rounded-full border border-[#dbe2ee] bg-white text-xs font-semibold text-[#3b82f6]">
                    {`0${index + 1}`}
                  </span>
                  <h3 className="font-display text-lg font-semibold">
                    {step.title}
                  </h3>
                </div>
                <p className="mt-3 text-sm text-[#6b7280]">{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="demo" className="mx-auto max-w-6xl px-6 pb-20">
          <div className="grid gap-10 md:grid-cols-[1.1fr_0.9fr]">
            <div className="rounded-3xl border border-[#e6e6e1] bg-white p-6 shadow-[0_25px_50px_-40px_rgba(15,23,42,0.35)]">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs uppercase tracking-[0.18em] text-[#9ca3af]">
                    Today
                  </p>
                  <p className="mt-2 font-display text-lg font-semibold">
                    Thursday, May 15
                  </p>
                </div>
                <div className="flex items-center gap-1 text-[#cbd5f5]">
                  <span className="h-2 w-2 rounded-full bg-[#cbd5f5]" />
                  <span className="h-2 w-2 rounded-full bg-[#9ca3af]" />
                  <span className="h-2 w-2 rounded-full bg-[#3b82f6]" />
                </div>
              </div>
              <div className="mt-6 space-y-4">
                {schedule.map((item) => (
                  <div key={item.title} className="flex items-start gap-4">
                    <div className="pt-2 text-xs text-[#9ca3af]">{item.time}</div>
                    <div
                      className={`flex-1 rounded-2xl border border-[#e6e6e1] p-4 ${item.tone}`}
                    >
                      <p className="text-sm font-semibold text-[#111111]">
                        {item.title}
                      </p>
                      <p className="mt-1 text-xs text-[#6b7280]">
                        {item.meta}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="rounded-3xl border border-[#e6e6e1] bg-white p-6 shadow-[0_25px_50px_-40px_rgba(15,23,42,0.35)]">
              <div className="flex items-center gap-2 text-sm font-semibold text-[#111111]">
                <span className="flex h-8 w-8 items-center justify-center rounded-full border border-[#e6e6e1] bg-[#f7f7f5] text-[#3b82f6]">
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                  >
                    <path d="M12 5v14M5 12h14" strokeLinecap="round" />
                  </svg>
                </span>
                Daily Briefing
              </div>
              <div className="mt-5 space-y-4 text-sm text-[#6b7280]">
                {briefings.map((brief) => (
                  <div
                    key={brief.title}
                    className="rounded-2xl border border-[#eef0ed] bg-[#f9fafb] p-4"
                  >
                    <p className="text-sm font-semibold text-[#111111]">
                      {brief.title}
                    </p>
                    <p className="mt-1 text-xs text-[#6b7280]">
                      {brief.detail}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section
          id="security"
          className="mx-auto max-w-6xl px-6 pb-20 pt-6 text-center"
        >
          <h2 className="font-display text-2xl font-semibold">
            Your schedule stays private.
          </h2>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-8 text-sm text-[#6b7280]">
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[#e8f6ef] text-[#10b981]">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                >
                  <path d="M12 4l7 4v5c0 4.4-3 7-7 7s-7-2.6-7-7V8l7-4z" />
                  <path d="M9 12l2 2 4-4" strokeLinecap="round" />
                </svg>
              </span>
              Encrypted at rest
            </div>
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[#e8f0fe] text-[#3b82f6]">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                >
                  <path d="M7 7h10v10H7z" />
                  <path d="M12 7v10" strokeLinecap="round" />
                </svg>
              </span>
              Export anytime
            </div>
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[#feeceb] text-[#ef4444]">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                >
                  <path d="M4 4l16 16" strokeLinecap="round" />
                  <circle cx="12" cy="12" r="7" />
                </svg>
              </span>
              No ad tracking
            </div>
          </div>
        </section>

        <section
          id="cta"
          className="mx-auto max-w-6xl px-6 pb-24 pt-6 text-center"
        >
          <h2 className="font-display text-3xl font-semibold tracking-tight">
            Start smarter scheduling today.
          </h2>
          <div className="mt-6 flex flex-col items-center gap-3">
            <a
              href="/calendar"
              className="rounded-full bg-[#3b82f6] px-8 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-[#3476de]"
            >
              Start free
            </a>
            <p className="text-xs text-[#9ca3af]">Cancel anytime.</p>
          </div>
        </section>
      </main>

      <footer className="border-t border-[#e6e6e1] bg-[#f7f7f5]">
        <div className="mx-auto flex max-w-6xl flex-col items-start gap-6 px-6 py-10 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-2 font-display font-semibold">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-[#e6e6e1] bg-white shadow-sm">
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
          </div>
          <div className="flex flex-wrap items-center gap-6 text-xs text-[#6b7280]">
            <a className="transition hover:text-[#111111]" href="#">
              Terms
            </a>
            <a className="transition hover:text-[#111111]" href="#">
              Privacy
            </a>
            <a className="transition hover:text-[#111111]" href="#">
              Contact
            </a>
            <a className="transition hover:text-[#111111]" href="#">
              Blog
            </a>
          </div>
        </div>
        <div className="mx-auto max-w-6xl px-6 pb-10 text-xs text-[#9ca3af]">
          (c) 2025 AI Calendar Inc. All rights reserved.
        </div>
      </footer>
    </div>
  );
}
