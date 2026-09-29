import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Ledger-Sync",
  description:
    "Self-hosted: bank CSV in, a balanced double-entry journal out, pushed to QuickBooks Online.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
