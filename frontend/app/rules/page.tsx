import type { Metadata } from "next";

import { RulesManager } from "./RulesManager";

export const metadata: Metadata = { title: "Categorization rules · Ledger-Sync" };

export default function RulesPage() {
  return <RulesManager />;
}
