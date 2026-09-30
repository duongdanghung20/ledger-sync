"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import styles from "./rules.module.css";

type Condition = { field: string; operator: string; value: string; value2?: string };
type Rule = {
  id: string;
  priority: number;
  target_qbo_account_id: string;
  conditions: Condition[];
  invalid_target: boolean;
};
type Account = { qbo_id: string; name: string };
type Targets = Record<string, Account[]>;
type Catalog = { rules: Rule[]; targets: Targets };
type Summary = { total: number; by_rule: number; manual: number; uncategorized: number };

type Draft = { id?: string; priority: number; target: string; conditions: Condition[] };
type View = "loading" | "ready" | "unauthorized" | "error";

const FIELDS = ["payee", "description", "amount", "date"] as const;
const OPS_FOR: Record<string, { value: string; label: string }[]> = {
  payee: [
    { value: "contains", label: "contains" },
    { value: "equals", label: "is exactly" },
  ],
  description: [
    { value: "contains", label: "contains" },
    { value: "equals", label: "is exactly" },
  ],
  amount: [
    { value: "lte", label: "at most (≤)" },
    { value: "gte", label: "at least (≥)" },
    { value: "between", label: "between" },
  ],
  date: [
    { value: "gte", label: "on or after" },
    { value: "lte", label: "on or before" },
    { value: "between", label: "between" },
  ],
};
const OP_SYMBOL: Record<string, string> = {
  contains: "contains",
  equals: "is",
  gte: "≥",
  lte: "≤",
  between: "between",
};

function inputType(field: string) {
  return field === "amount" ? "number" : field === "date" ? "date" : "text";
}

function flatten(targets: Targets): Account[] {
  return Object.values(targets).flat();
}

export function RulesManager() {
  const [view, setView] = useState<View>("loading");
  const [catalog, setCatalog] = useState<Catalog>({ rules: [], targets: {} });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [formError, setFormError] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/categorization/rules");
      if (res.status === 401) return setView("unauthorized");
      if (!res.ok) return setView("error");
      setCatalog(await res.json());
      setView("ready");
    } catch {
      setView("error");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const accounts = flatten(catalog.targets);
  const accountName = (id: string) => accounts.find((a) => a.qbo_id === id)?.name ?? id;
  const noTargets = accounts.length === 0;
  const rules = [...catalog.rules].sort((a, b) => a.priority - b.priority);

  function startAdd() {
    setFormError("");
    const nextPriority = rules.length ? Math.max(...rules.map((r) => r.priority)) + 10 : 10;
    setDraft({
      priority: nextPriority,
      target: accounts[0]?.qbo_id ?? "",
      conditions: [{ field: "payee", operator: "contains", value: "" }],
    });
  }

  function startEdit(rule: Rule) {
    setFormError("");
    setDraft({
      id: rule.id,
      priority: rule.priority,
      target: rule.target_qbo_account_id,
      conditions: rule.conditions.map((c) => ({ ...c })),
    });
  }

  async function saveDraft() {
    if (!draft) return;
    setFormError("");
    setBusy(true);
    try {
      const body = {
        priority: draft.priority,
        target_qbo_account_id: draft.target,
        conditions: draft.conditions,
      };
      const res = draft.id
        ? await fetch(`/api/categorization/rules/${draft.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          })
        : await fetch("/api/categorization/rules", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
      if (res.status === 401) return setView("unauthorized");
      if (res.status === 422) {
        const { detail } = await res.json();
        setFormError(typeof detail === "string" ? detail : "Check this rule and try again.");
        return;
      }
      if (!res.ok) throw new Error(String(res.status));
      setCatalog(await res.json());
      setDraft(null);
    } catch {
      setFormError("Couldn't save the rule. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function deleteRule(id: string) {
    setBusy(true);
    try {
      const res = await fetch(`/api/categorization/rules/${id}`, { method: "DELETE" });
      if (res.status === 401) return setView("unauthorized");
      await load();
      if (draft?.id === id) setDraft(null);
    } finally {
      setBusy(false);
    }
  }

  // Reorder is a priority swap with the adjacent rule — two writes, then reload.
  async function move(index: number, dir: -1 | 1) {
    const a = rules[index];
    const b = rules[index + dir];
    if (!a || !b) return;
    setBusy(true);
    try {
      await fetch(`/api/categorization/rules/${a.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ priority: b.priority }),
      });
      await fetch(`/api/categorization/rules/${b.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ priority: a.priority }),
      });
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    setBusy(true);
    setSummary(null);
    try {
      const res = await fetch("/api/categorization/run", { method: "POST" });
      if (res.status === 401) return setView("unauthorized");
      if (!res.ok) throw new Error(String(res.status));
      setSummary(await res.json());
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={styles.wrap}>
      <Link href="/" className={styles.back}>
        Ledger-Sync
      </Link>
      <h1 className={styles.title}>Categorization rules</h1>
      <p className={styles.intro}>
        Rules read top to bottom. The first rule whose conditions all match assigns a
        transaction&rsquo;s account, and evaluation stops there. A hand-picked account always
        wins over a rule, even after you run categorization again.
      </p>

      {view === "loading" && <p className={styles.muted}>Loading…</p>}

      {view === "unauthorized" && (
        <div className={styles.state}>
          <p className={styles.stateLead}>Sign in first</p>
          <p className={styles.muted}>
            <Link href="/login" className={styles.inlineLink}>
              Sign in
            </Link>{" "}
            to manage categorization rules.
          </p>
        </div>
      )}

      {view === "error" && (
        <p className={styles.err} role="alert">
          Couldn&rsquo;t reach the server. Try again.
        </p>
      )}

      {view === "ready" && (
        <>
          {noTargets && (
            <div className={styles.state}>
              <p className={styles.stateLead}>No accounts to post to yet</p>
              <p className={styles.muted}>
                Rules assign a QuickBooks account.{" "}
                <Link href="/settings/quickbooks" className={styles.inlineLink}>
                  Connect QuickBooks
                </Link>{" "}
                to pull in your chart of accounts, then add a rule.
              </p>
            </div>
          )}

          {rules.length === 0 && !noTargets && draft === null && (
            <div className={styles.state}>
              <p className={styles.stateLead}>No rules yet</p>
              <p className={styles.muted}>
                Add a rule to route transactions to an account automatically. Anything no rule
                matches waits for you to categorize it by hand.
              </p>
            </div>
          )}

          {rules.length > 0 && (
            <ol className={styles.rulebook}>
              {rules.map((rule, i) => (
                <li key={rule.id} className={styles.rule}>
                  <span className={styles.order} aria-hidden="true">
                    {i + 1}
                  </span>
                  <div className={styles.clause}>
                    <p className={styles.clauseText}>
                      <span className={styles.when}>When</span>{" "}
                      {rule.conditions.map((c, ci) => (
                        <span key={ci}>
                          {ci > 0 && <span className={styles.and}> and </span>}
                          <span className={styles.field}>{c.field}</span>{" "}
                          <span className={styles.op}>{OP_SYMBOL[c.operator]}</span>{" "}
                          <span className={styles.val}>
                            {c.value}
                            {c.operator === "between" && ` – ${c.value2}`}
                          </span>
                        </span>
                      ))}
                      <span className={styles.post}>, post to </span>
                      <span className={styles.account}>{accountName(rule.target_qbo_account_id)}</span>
                    </p>
                    {rule.invalid_target && (
                      <p className={styles.flag}>
                        That account is inactive in QuickBooks — this rule is skipped until you
                        point it at an active account.
                      </p>
                    )}
                  </div>
                  <div className={styles.rowActions}>
                    <button
                      type="button"
                      className={styles.icon}
                      onClick={() => move(i, -1)}
                      disabled={i === 0 || busy}
                      aria-label={`Move rule ${i + 1} up`}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className={styles.icon}
                      onClick={() => move(i, 1)}
                      disabled={i === rules.length - 1 || busy}
                      aria-label={`Move rule ${i + 1} down`}
                    >
                      ↓
                    </button>
                    <button type="button" className={styles.textBtn} onClick={() => startEdit(rule)}>
                      Edit
                    </button>
                    <button
                      type="button"
                      className={styles.textBtn}
                      onClick={() => deleteRule(rule.id)}
                      disabled={busy}
                    >
                      Delete
                    </button>
                  </div>
                </li>
              ))}
            </ol>
          )}

          {draft ? (
            <RuleForm
              draft={draft}
              setDraft={setDraft}
              targets={catalog.targets}
              onSave={saveDraft}
              onCancel={() => setDraft(null)}
              error={formError}
              busy={busy}
            />
          ) : (
            !noTargets && (
              <button type="button" className={styles.addBtn} onClick={startAdd}>
                Add a rule
              </button>
            )
          )}

          {rules.length > 0 && (
            <>
              <div className={styles.close} aria-hidden="true" />
              <div className={styles.runBar}>
                <button type="button" className={styles.run} onClick={run} disabled={busy}>
                  {busy ? "Working…" : "Run categorization"}
                </button>
                <span className={styles.runHint}>
                  Applies these rules to every imported transaction. Your hand-picked accounts stay
                  put.
                </span>
              </div>
              {summary && <RunResult summary={summary} />}
            </>
          )}
        </>
      )}
    </main>
  );
}

function RuleForm({
  draft,
  setDraft,
  targets,
  onSave,
  onCancel,
  error,
  busy,
}: {
  draft: Draft;
  setDraft: (d: Draft) => void;
  targets: Targets;
  onSave: () => void;
  onCancel: () => void;
  error: string;
  busy: boolean;
}) {
  function setCondition(i: number, patch: Partial<Condition>) {
    const conditions = draft.conditions.map((c, ci) => (ci === i ? { ...c, ...patch } : c));
    setDraft({ ...draft, conditions });
  }
  function changeField(i: number, field: string) {
    // Field type changes which operators are valid — reset to the first valid one.
    setCondition(i, { field, operator: OPS_FOR[field][0].value, value: "", value2: "" });
  }
  function addCondition() {
    setDraft({
      ...draft,
      conditions: [...draft.conditions, { field: "payee", operator: "contains", value: "" }],
    });
  }
  function removeCondition(i: number) {
    setDraft({ ...draft, conditions: draft.conditions.filter((_, ci) => ci !== i) });
  }

  return (
    <section className={styles.form} aria-label={draft.id ? "Edit rule" : "New rule"}>
      <h2 className={styles.formHead}>{draft.id ? "Edit rule" : "New rule"}</h2>

      <p className={styles.formLabel}>When all of these match</p>
      <ul className={styles.condList}>
        {draft.conditions.map((c, i) => (
          <li key={i} className={styles.condRow}>
            <select
              aria-label={`Condition ${i + 1} field`}
              className={styles.select}
              value={c.field}
              onChange={(e) => changeField(i, e.target.value)}
            >
              {FIELDS.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
            <select
              aria-label={`Condition ${i + 1} operator`}
              className={styles.select}
              value={c.operator}
              onChange={(e) => setCondition(i, { operator: e.target.value })}
            >
              {OPS_FOR[c.field].map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <input
              aria-label={`Condition ${i + 1} value`}
              className={styles.input}
              type={inputType(c.field)}
              step={c.field === "amount" ? "0.01" : undefined}
              value={c.value}
              onChange={(e) => setCondition(i, { value: e.target.value })}
            />
            {c.operator === "between" && (
              <input
                aria-label={`Condition ${i + 1} upper value`}
                className={styles.input}
                type={inputType(c.field)}
                step={c.field === "amount" ? "0.01" : undefined}
                value={c.value2 ?? ""}
                onChange={(e) => setCondition(i, { value2: e.target.value })}
              />
            )}
            {draft.conditions.length > 1 && (
              <button
                type="button"
                className={styles.icon}
                onClick={() => removeCondition(i)}
                aria-label={`Remove condition ${i + 1}`}
              >
                ×
              </button>
            )}
          </li>
        ))}
      </ul>
      <button type="button" className={styles.textBtn} onClick={addCondition}>
        + Add condition
      </button>
      <p className={styles.orHint}>
        Every condition must match. To match either of two things, write a second rule.
      </p>

      <div className={styles.formField}>
        <label htmlFor="rule-target">Post to account</label>
        <select
          id="rule-target"
          className={styles.select}
          value={draft.target}
          onChange={(e) => setDraft({ ...draft, target: e.target.value })}
        >
          {Object.entries(targets).map(([classification, accounts]) => (
            <optgroup key={classification} label={classification}>
              {accounts.map((a) => (
                <option key={a.qbo_id} value={a.qbo_id}>
                  {a.name}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>

      {error && (
        <p className={styles.err} role="alert">
          {error}
        </p>
      )}

      <div className={styles.formActions}>
        <button type="button" className={styles.run} onClick={onSave} disabled={busy}>
          {draft.id ? "Save rule" : "Add rule"}
        </button>
        <button type="button" className={styles.textBtn} onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </section>
  );
}

function RunResult({ summary }: { summary: Summary }) {
  return (
    <section className={styles.result} aria-label="Categorization result" aria-live="polite">
      <h2 className={styles.resultHead}>Last run</h2>
      <dl className={styles.ledger}>
        <ResultRow label="Transactions" value={summary.total} />
        <ResultRow label="Matched by a rule" value={summary.by_rule} />
        <ResultRow label="Kept your hand-picked account" value={summary.manual} />
        <ResultRow
          label="Waiting to be categorized"
          value={summary.uncategorized}
          flag={summary.uncategorized > 0}
        />
      </dl>
      <div className={styles.ledgerClose} aria-hidden="true" />
    </section>
  );
}

function ResultRow({ label, value, flag }: { label: string; value: number; flag?: boolean }) {
  return (
    <div className={styles.resRow}>
      <dt className={styles.resLabel}>{label}</dt>
      <span className={styles.leader} aria-hidden="true" />
      <dd className={`${styles.resValue} ${flag ? styles.flagged : ""}`}>{value}</dd>
    </div>
  );
}
