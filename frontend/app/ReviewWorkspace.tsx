"use client";

/**
 * The review surface's layout router. All three views read the same `/api/review`
 * model and drive the same four operations (both live in `@/lib/review`); this
 * component only decides which layout is on screen and remembers the choice.
 *
 * Each view mounts on its own and calls `useReview()` itself, so switching is
 * layout, never a different feature. The chosen layout persists via localStorage
 * (`usePreferredLayout`).
 */

import { type ReviewLayout, usePreferredLayout } from "../lib/review";
import { FocusQueue } from "./FocusQueue";
import { LedgerReview } from "./LedgerReview";
import { PipelineBoard } from "./PipelineBoard";
import styles from "./workspace.module.css";

const OPTIONS: { key: ReviewLayout; label: string }[] = [
  { key: "ledger", label: "Ledger" },
  { key: "focus", label: "Focus" },
  { key: "pipeline", label: "Pipeline" },
];

function ViewSwitcher({
  current,
  onChange,
}: {
  current: ReviewLayout;
  onChange: (next: ReviewLayout) => void;
}) {
  return (
    <div className={styles.switcher} role="tablist" aria-label="Review layout">
      {OPTIONS.map((o) => (
        <button
          key={o.key}
          type="button"
          role="tab"
          aria-selected={current === o.key}
          className={`${styles.opt} ${current === o.key ? styles.optActive : ""}`}
          onClick={() => onChange(o.key)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function ReviewWorkspace() {
  const [layout, choose] = usePreferredLayout();
  const switcher = <ViewSwitcher current={layout} onChange={choose} />;

  if (layout === "focus") return <FocusQueue switcher={switcher} />;
  if (layout === "pipeline") return <PipelineBoard switcher={switcher} />;
  return <LedgerReview switcher={switcher} />;
}
