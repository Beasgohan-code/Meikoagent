import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: "MEIKO AGENT — Telegram control plane",
  description:
    "MEIKO AGENT is a full-stack Telegram ecosystem: moderation, plugins, multi-bot hosting, automation and analytics.",
  applicationName: "MEIKO AGENT",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  themeColor: "#0c0d12",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-charcoal-900 font-sans antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
