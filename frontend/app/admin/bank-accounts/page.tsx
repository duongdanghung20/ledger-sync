import type { Metadata } from "next";

import { BankAccountsAdmin } from "./BankAccountsAdmin";

export const metadata: Metadata = { title: "Bank accounts · Ledger-Sync" };

export default function AdminBankAccountsPage() {
  return <BankAccountsAdmin />;
}
