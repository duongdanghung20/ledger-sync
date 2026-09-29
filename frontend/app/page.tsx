import { LedgerRow } from "../components/LedgerRow";

const services = [
  { label: "Web", value: "Next.js" },
  { label: "API", value: "/api" },
  { label: "Database", value: "PostgreSQL" },
  { label: "Proxy", value: "one origin" },
];

export default function Page() {
  return (
    <main className="page">
      <header className="masthead">
        <h1 className="wordmark">Ledger-Sync</h1>
        <p className="statement">
          Self-hosted. Bank CSV in, a balanced double-entry journal out, pushed to
          QuickBooks Online.
        </p>
      </header>

      <section className="ledger" aria-label="Running services">
        {services.map((s) => (
          <LedgerRow key={s.label} label={s.label} value={s.value} />
        ))}
        <div className="ledger-close" aria-hidden="true" />
        <p className="ledger-caption">The books balance. Feature slices land on top.</p>
      </section>
    </main>
  );
}
