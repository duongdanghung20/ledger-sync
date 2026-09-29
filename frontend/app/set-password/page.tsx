import type { Metadata } from "next";

import { AuthShell } from "../../components/AuthShell";
import { SetPasswordForm } from "../../components/SetPasswordForm";

export const metadata: Metadata = { title: "Set your password · Ledger-Sync" };

export default async function SetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  return (
    <AuthShell
      title="Set your password"
      intro="Choose a password to finish setting up your account. This link works once."
    >
      <SetPasswordForm token={token ?? ""} />
    </AuthShell>
  );
}
