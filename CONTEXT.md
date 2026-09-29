# Ledger-Sync

The domain of a self-hosted tool that turns bank CSV exports into a balanced double-entry journal and pushes it to QuickBooks Online. One instance serves one Organization, whose financial data is organized into one or more Books.

## Language

**Organization**:
The single tenant an instance serves. Owns the Users and the Books; financial records belong to a Book, not directly to the Organization.
_Avoid_: Tenant, company, account

**User**:
An authenticated human with access to the Organization, holding one Role.
_Avoid_: Member, login

**Role**:
A permission set held by a User, applied Organization-wide across all Books. One of Admin or Bookkeeper.
_Avoid_: Permission-level, group

**Book**:
A distinct set of accounting books within an Organization — one accounting/legal entity. Owns its own QuickBooks Connection, Chart of Accounts, and Bank Accounts; every financial record is Book-scoped.
_Avoid_: Entity, company, ledger, set-of-books

**Bank Account**:
A source of transactions within a Book, such as a checking or card account. Every Imported Transaction carries its source Bank Account. Maps to exactly one Account in the Book's Chart of Accounts — the cash/ledger side of every Journal Entry built from its transactions.
_Avoid_: Account (that means the QuickBooks ledger account), source

**Account**:
A single QuickBooks ledger account (asset, liability, income, expense, or equity) that a Journal Entry posts against. Sourced from the Book's QuickBooks company, not defined locally.
_Avoid_: Category, GL code, bank account

**Chart of Accounts**:
The full set of Accounts for a Book, owned by that Book's QuickBooks company and mirrored into Ledger-Sync.
_Avoid_: CoA (spell it out in prose), account list

**QuickBooks Connection**:
The OAuth link from a Book to exactly one QuickBooks company, identified by its `realmID`. One Connection per Book.
_Avoid_: Integration, QBO link

**Imported Transaction**:
One row from a bank CSV export, parsed into canonical fields and scoped to a Book and its source Bank Account. Not yet a journal record.
_Avoid_: Entry, line, txn (in prose)

**Categorization Rule**:
A deterministic, Book-scoped rule mapping Imported Transaction fields to an Account. Manual override applies where no rule matches.
_Avoid_: Filter, classifier

**Journal Entry**:
A balanced double-entry record (debits equal credits) produced from a categorized Imported Transaction and destined for the Book's QuickBooks company.
_Avoid_: Posting, transaction (that's the bank side)

**Column-Mapping Profile**:
A saved, Book-scoped mapping of one bank's CSV columns to canonical fields, reused across imports from that bank.
_Avoid_: Template, format, schema

**Sync Status**:
The state of a Journal Entry's push to QuickBooks: pending, posted, or failed.
_Avoid_: State, sync flag

**Session**:
A User's active authenticated login, identified by an opaque id held in an httpOnly cookie and stored server-side. Revoked when the User is disabled or their Role changes.
_Avoid_: Token, JWT, login (that's the act)

**Invitation**:
An Admin-issued grant that lets one person become a User with a chosen Role, redeemed via a single-use, expiring set-password link delivered out-of-band. The same link primitive is reused for Admin-triggered password resets.
_Avoid_: Signup, registration, invite email
