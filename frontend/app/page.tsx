import type { Metadata } from "next";

import { ReviewWorkspace } from "./ReviewWorkspace";

export const metadata: Metadata = { title: "Review · Ledger-Sync" };

export default function Page() {
  return <ReviewWorkspace />;
}
