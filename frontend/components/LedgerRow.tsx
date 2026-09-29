export function LedgerRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="ledger-row">
      <span className="ledger-label">{label}</span>
      <span className="ledger-leader" aria-hidden="true" />
      <span className="ledger-value">{value}</span>
    </div>
  );
}
