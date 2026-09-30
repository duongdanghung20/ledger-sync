"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import styles from "./quickbooks.module.css";

type Status = "pending" | "connected" | "disconnected";
type Connection = { status: Status; realm_id: string | null; connected_at: string | null };

// Where the fetch left us. "ready" carries a Connection; the rest are terminal messages.
type View = "loading" | "ready" | "forbidden" | "unauthorized" | "error";

// The browser must do a real navigation to /api/qbo/authorize — the backend answers
// with a cross-origin 307 to Intuit's consent screen, so this is a plain <a>, not a fetch.
const AUTHORIZE = "/api/qbo/authorize";

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
}

export function QuickBooksConnection({ banner }: { banner?: "connected" | "error" }) {
  const [view, setView] = useState<View>("loading");
  const [conn, setConn] = useState<Connection | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/api/qbo/status");
        if (!alive) return;
        if (res.status === 403) return setView("forbidden");
        if (res.status === 401) return setView("unauthorized");
        if (!res.ok) return setView("error");
        setConn(await res.json());
        setView("ready");
      } catch {
        if (alive) setView("error");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  return (
    <main className={styles.wrap}>
      <Link href="/" className={styles.back}>
        Ledger-Sync
      </Link>
      <h1 className={styles.title}>QuickBooks</h1>
      <p className={styles.intro}>
        Link this book to your QuickBooks Online company so approved journal entries post
        straight to your books. Only an admin manages the connection.
      </p>

      {banner === "connected" && (
        <p className={styles.ok} role="status">
          Connected to QuickBooks.
        </p>
      )}
      {banner === "error" && (
        <p className={styles.err} role="alert">
          That authorization didn&rsquo;t finish. Nothing changed — start it again below.
        </p>
      )}

      <div className={styles.close} aria-hidden="true" />

      {view === "loading" && <p className={styles.muted}>Checking the connection…</p>}

      {view === "forbidden" && (
        <div className={styles.state}>
          <p className={styles.stateLead}>Admin only</p>
          <p className={styles.muted}>Ask an admin to connect QuickBooks for this book.</p>
        </div>
      )}

      {view === "unauthorized" && (
        <div className={styles.state}>
          <p className={styles.stateLead}>Sign in first</p>
          <p className={styles.muted}>
            <Link href="/login" className={styles.inlineLink}>
              Sign in
            </Link>{" "}
            as an admin to manage the QuickBooks connection.
          </p>
        </div>
      )}

      {view === "error" && (
        <p className={styles.err} role="alert">
          Couldn&rsquo;t reach the server. Try again.
        </p>
      )}

      {view === "ready" && conn?.status === "pending" && (
        <div className={styles.state}>
          <p className={styles.muted}>No QuickBooks company is connected yet.</p>
          <a className={styles.action} href={AUTHORIZE}>
            Connect to QuickBooks
          </a>
        </div>
      )}

      {view === "ready" && conn?.status === "connected" && (
        <>
          <dl className={styles.ledger}>
            <div className={styles.row}>
              <dt className={styles.label}>Company</dt>
              <span className={styles.leader} aria-hidden="true" />
              <dd className={styles.value}>{conn.realm_id ?? "—"}</dd>
            </div>
            <div className={styles.row}>
              <dt className={styles.label}>Connected</dt>
              <span className={styles.leader} aria-hidden="true" />
              <dd className={styles.value}>{formatWhen(conn.connected_at)}</dd>
            </div>
            <div className={styles.row}>
              <dt className={styles.label}>Status</dt>
              <span className={styles.leader} aria-hidden="true" />
              <dd className={styles.value}>Connected</dd>
            </div>
          </dl>
          <p className={styles.reconnectNote}>
            Need to relink or switch companies?{" "}
            <a className={styles.inlineLink} href={AUTHORIZE}>
              Reconnect
            </a>
            .
          </p>
        </>
      )}

      {view === "ready" && conn?.status === "disconnected" && (
        <div className={styles.state}>
          <div className={styles.blocked} role="status">
            <p className={styles.blockedLead}>Connection lost</p>
            <p>
              Journal pushes are paused until you reconnect. Nothing else changes — reconnect
              to resume posting to QuickBooks.
            </p>
          </div>
          {conn.realm_id && (
            <p className={styles.muted}>Last linked to company {conn.realm_id}.</p>
          )}
          <a className={styles.action} href={AUTHORIZE}>
            Reconnect to QuickBooks
          </a>
        </div>
      )}
    </main>
  );
}
