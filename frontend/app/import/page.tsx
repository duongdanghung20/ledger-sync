import type { Metadata } from "next";

import { ImportReview } from "./ImportReview";

export const metadata: Metadata = { title: "Import transactions · Ledger-Sync" };

export default function ImportPage() {
  return <ImportReview />;
}
