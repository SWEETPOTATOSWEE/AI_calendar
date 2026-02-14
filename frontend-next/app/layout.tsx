import type { Metadata } from "next";
import "./globals.css";
import ThemeTokenProvider from "./calendar/components/ThemeTokenProvider";

export const metadata: Metadata = {
  title: {
    default: "OptCal",
    template: "%s | OptCal",
  },
  description: "캘린더 프론트엔드",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600;700&family=Noto+Sans+KR:wght@300;400;500;700;900&family=Space+Grotesk:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body suppressHydrationWarning className="min-h-screen font-sans antialiased">
        <ThemeTokenProvider />
        {children}
      </body>
    </html>
  );
}
