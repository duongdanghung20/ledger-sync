import type { Metadata } from "next";

import { AuthShell } from "../../components/AuthShell";
import { LoginForm } from "../../components/LoginForm";

export const metadata: Metadata = { title: "Sign in · Ledger-Sync" };

export default function LoginPage() {
  return (
    <AuthShell title="Sign in" intro="Enter your credentials to open the books.">
      <LoginForm />
    </AuthShell>
  );
}
