import Link from "next/link";

export function AuthShell({
  title,
  intro,
  children,
}: {
  title: string;
  intro: string;
  children: React.ReactNode;
}) {
  return (
    <main className="auth">
      <Link href="/" className="auth-wordmark">
        Ledger-Sync
      </Link>
      <h1 className="auth-title">{title}</h1>
      <p className="auth-intro">{intro}</p>
      {children}
    </main>
  );
}
