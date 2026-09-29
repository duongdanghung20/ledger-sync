# CSV import, column mapping, and dedup

Type: prototype
Status: resolved

## Question

Design CSV import: arbitrary bank exports parsed via a saved Column-Mapping Profile, with dedup on re-import. An import targets one Book and one source Bank Account (first-class, Book-scoped); every Imported Transaction carries that Bank Account.

- Column-Mapping Profile shape: map a bank's CSV columns to canonical fields (date, amount, description, optional payee). Handle sign conventions — separate debit/credit columns vs a single signed amount.
- Reuse: how a profile is selected or detected for a given import.
- Bank Account identity: how the source Bank Account is chosen or created at import, and how re-imports attach to the same one.
- Dedup: the key (hash of which fields?) and behaviour when re-importing overlapping rows (skip duplicates, never double-import).
- Malformed or partial rows.
- Prototype parsing + dedup against 2–3 real bank export samples.

Correctness: dedup must never drop distinct transactions nor double-import. HITL prototype; keep it on record.

## Answer

Resolved via the drivable logic prototype [`05-csv-import-prototype.html`](05-csv-import-prototype.html) plus a headless multiset-dedup check (7/7 assertions: never drops distinct, never double-imports). Three human rulings locked the design: (1) external-id-first / content-multiset dedup, (2) resolve on synthetic samples with real-sample validation deferred to build, (3) canonical amount is signed with **negative = money out**.

### Canonical Imported Transaction

One parsed row, scoped to a Book and a Bank Account. Fields:

- `date` — ISO 8601 (`YYYY-MM-DD`).
- `amount` — signed decimal. **Negative = money out (outflow) of the Bank Account, positive = money in.** Consistent with ticket 04 (categorization matches the signed canonical amount).
- `description` — required.
- `payee` — optional.
- `external_id` — optional; the bank's stable unique row id when the export provides one.
- Scoping / provenance (not from the CSV): `book_id`, `bank_account_id`, `column_mapping_profile_id`, `imported_at`, and the raw source row retained for audit.

### Column-Mapping Profile

Saved, Book-scoped, one per bank. Shape (validated in the prototype against all three samples):

```jsonc
{
  "name": "Chase Checking",
  "bank_account": "<default Bank Account id>",  // profile default; overridable per import
  "delimiter": ",",              // e.g. ";" for EU exports
  "decimal": "dot",             // "dot" (1,234.56) or "comma" (1.234,56)
  "header_row": 0,
  "skip_trailing": 0,            // drop N trailing summary rows
  "date": { "column": "Posting Date", "format": "MM/DD/YYYY" }, // or DD/MM/YYYY, YYYY-MM-DD
  "description": { "column": "Description" },
  "payee": { "column": "Counterparty" },          // optional
  "external_id": { "column": "Transaction ID" },  // optional
  "amount": { "mode": "signed", "column": "Amount", "flip": false }
  // OR: "amount": { "mode": "debit_credit", "debit_column": "Debit", "credit_column": "Credit" }
}
```

- **Sign conventions handled two ways.** `signed` mode: one column, optional `flip` when the bank's sign is inverted from our convention. `debit_credit` mode: a debit column = money out (mapped to negative), a credit column = money in (positive).
- **Profile selection / detection.** On import, auto-suggest the profile whose *header signature* (normalized set of column names) matches the uploaded CSV; user confirms, or picks/creates a profile. No content sniffing needed.

### Bank Account identity

Bank Account is first-class and Book-scoped; every Imported Transaction carries its `bank_account_id`. At import the user targets one Book and one Bank Account: the profile carries a default Bank Account, overridable per import, and a new Bank Account can be created inline. Re-imports attach to the same Bank Account via the profile default or explicit choice. **Dedup is scoped per Bank Account** — the same date/amount/description in two different Bank Accounts are distinct transactions.

### Dedup (the correctness crux)

Content alone cannot distinguish "same transaction re-exported in an overlapping file" from "two genuinely distinct identical transactions" (two $5 coffees, same day). Ruling:

- **When the bank supplies a stable unique id** → map it to `external_id` and dedup on `(bank_account_id, external_id)` alone. Bulletproof: distinct rows with distinct ids are kept; a re-import of the same rows is skipped. *Always preferred.*
- **When it does not** → dedup key = `hash(date, signed amount, description, payee)`, reconciled as a **multiset per Bank Account**: an incoming row is a duplicate only if an already-imported row with the same key is still unmatched; extras beyond the existing multiplicity are new. Guarantees on re-import of overlapping files: no double-import, and same-day genuine identicals are all retained.
- **Surface, don't guess.** When content-keyed rows in a single file collide, show the user "N possible duplicates within this file" (input to the review UI, ticket 09) rather than silently deciding.

**Known ceiling** (`ponytail:` global-lock class): without a bank unique id, re-imports across non-aligned windows are irreducibly ambiguous. Optional mitigation — map a running-`Balance` column (present in the Chase sample) as an extra key component when available. Do not silently guess; inform the user.

### Malformed / partial rows

Per-row validation: date parseable against the profile format, amount present and numeric (either mode), description non-empty. Failures go to a **rejected list with a reason** — never imported, never silently dropped; valid rows in the same file still import; the user reviews rejects. Parsing is RFC 4180-style (quoted fields with embedded delimiters/newlines, CRLF, BOM strip), configurable delimiter, dot/comma decimals, parentheses = negative, currency/thousands separators stripped.

### For the build

- Backend CSV parsing uses **Python stdlib `csv`** (no new dependency); the prototype hand-rolls a parser only to stay single-file/offline.
- Real bank-export validation (per the user's ruling) is deferred into implementation — feed 2-3 real exports through the mapping + dedup before shipping.
- The pure map-and-dedup logic is liftable straight from the prototype; it belongs on the import path, run per (Book, Bank Account, profile).
