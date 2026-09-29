import type { Metadata } from "next";

import { UsersAdmin } from "./UsersAdmin";

export const metadata: Metadata = { title: "People · Ledger-Sync" };

export default function AdminUsersPage() {
  return <UsersAdmin />;
}
