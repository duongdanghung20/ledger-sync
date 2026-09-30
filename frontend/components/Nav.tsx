import Link from "next/link";

import styles from "./nav.module.css";

/**
 * Shared top nav for the signed-in work surfaces. Several feature screens
 * (rules, import, admin, QuickBooks settings) had no entry point; this links
 * them from the review surface. Reused wherever a signed-in page needs wayfinding.
 */
const LINKS = [
  { href: "/import", label: "Import" },
  { href: "/rules", label: "Rules" },
  { href: "/admin/bank-accounts", label: "Bank accounts" },
  { href: "/admin/users", label: "Users" },
  { href: "/settings/quickbooks", label: "QuickBooks" },
];

export function Nav() {
  return (
    <nav className={styles.nav} aria-label="Sections">
      <Link href="/" className={styles.home}>
        Ledger-Sync
      </Link>
      <span className={styles.rule} aria-hidden="true" />
      <ul className={styles.links}>
        {LINKS.map((l) => (
          <li key={l.href}>
            <Link href={l.href} className={styles.link}>
              {l.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
