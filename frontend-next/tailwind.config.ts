import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: "#137fec",
        "background-light": "oklch(var(--bg-canvas))",
        "background-dark": "oklch(var(--bg-canvas))",
        // Semantic Tokens
        "bg-canvas": "oklch(var(--bg-canvas))",
        "bg-surface": "oklch(var(--bg-surface))",
        "bg-subtle": "oklch(var(--bg-subtle))",
        "text-primary": "oklch(var(--text-primary))",
        "text-secondary": "oklch(var(--text-secondary))",
        "text-disabled": "oklch(var(--text-disabled))",
        "border-subtle": "oklch(var(--border-subtle))",
        "border-strong": "oklch(var(--border-strong))",
        "token-primary": "oklch(var(--primary))",
        "token-primary-hover": "oklch(var(--primary-hover))",
        "token-primary-low": "oklch(var(--primary-low))",
        "token-success": "oklch(var(--success))",
        "token-error": "oklch(var(--error))",
        "token-warning": "oklch(var(--warning))",
        "token-info": "oklch(var(--info))",
        "token-overlay": "oklch(var(--overlay))",
        "token-focus": "oklch(var(--focus))",
      },
      fontFamily: {
        display: ["\"Space Grotesk\"", "Noto Sans KR", "sans-serif"],
        sans: ["Noto Sans KR", "sans-serif"],
        plex: ["\"IBM Plex Sans\"", "Noto Sans KR", "sans-serif"],
        mono: [
          "\"IBM Plex Mono\"",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
    },
  },
};

export default config;
