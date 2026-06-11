import type { Metadata } from "next";
import type { JSX, ReactNode } from "react";

import { ToastProvider } from "@/components/Toast";

import "./globals.css";

export const metadata: Metadata = {
  title: "Genesis · Consciousness Stream",
  description:
    "Genesis Experiment dashboard — PLAYBACK widget for Phase 2 Day 4 first-Twitter-mistake demo arc.",
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "dark" as const,
  themeColor: "#0B1426",
};

export default function RootLayout({
  children,
}: {
  children: ReactNode;
}): JSX.Element {
  return (
    <html lang="en">
      <body>
        {/* G4 a11y — first focusable element in the shell: lets keyboard +
            screen-reader users jump past the chrome straight to the page's
            <main id="main-content">. Visually hidden until focused. */}
        <a href="#main-content" className="skip-to-content">
          skip to content
        </a>
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
