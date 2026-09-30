import type { Metadata } from "next";

import { QuickBooksConnection } from "./QuickBooksConnection";

export const metadata: Metadata = { title: "QuickBooks · Ledger-Sync" };

// The OAuth callback lands the browser back here with ?connected=1 or ?error=exchange.
// searchParams is a Promise in Next 16 — await it in the server page and hand the
// client component a plain flag, so it needs no Suspense boundary.
export default async function QuickBooksSettingsPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  const banner = params.connected ? "connected" : params.error ? "error" : undefined;
  return <QuickBooksConnection banner={banner} />;
}
