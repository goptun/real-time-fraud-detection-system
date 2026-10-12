## Why

The live dashboard showed the same transaction twice in a row ("01:30:03 Supermercado Curitiba R$ 56,81"), despite the project's `streaming-scoring-pipeline` capability declaring idempotent processing. Investigation before writing this proposal (see Root Cause) found the duplicate is a real bug in how the frontend combines the initial REST snapshot with the live SSE stream — not a persistence or simulator defect.

## Root Cause

Three candidate areas were investigated, as flagged in the audit:

1. **Simulator** (`app/data/simulator.py`): transaction IDs are generated from a monotonically incrementing counter (`tid = f"{self.cfg.id_prefix}_{self._counter:08d}"`) — no collision under normal single-instance operation. **Ruled out.**
2. **Consumer/persistence** (`app/storage/db.py`, `app/streaming/scorer.py`): `transactions.transaction_id` is the table's `PRIMARY KEY`, and inserts use `ON CONFLICT (transaction_id) DO NOTHING` (`Database.insert_transaction`, referenced as "Insert idempotente por transaction_id"). A redelivered event cannot produce a second row. **Ruled out** — confirms the existing `streaming-scoring-pipeline` "Idempotent processing" requirement already holds at the storage layer.
3. **Frontend** (`app/static/app.js`, `app/api/services.py`): **confirmed as the cause.**
   - `Broadcaster` (`app/api/services.py`) is a single, server-wide poller with one shared `cursor`, initialized once at startup to `db.max_seq()` and advanced by a background loop that calls `db.since_seq(cursor, 200)` roughly once per second.
   - Every SSE client gets a **new, empty queue** on `subscribe()` — there is no per-client "I've already seen up to seq N" state. A client receives whatever the shared `Broadcaster` pushes to its queue *from the moment it subscribes onward*, regardless of what that specific client already has.
   - `app.js`'s `init()` calls `api('transactions?limit=200')` (a REST snapshot via `Database.recent()`) and only *after* that response resolves does it call `connectStream()`, which opens the `EventSource`.
   - Because the `Broadcaster`'s poll interval is ~1 second, a transaction can already be persisted (and therefore already included in the REST snapshot) while the `Broadcaster`'s cursor hasn't reached it yet. If a client's SSE subscription is established during that window, the `Broadcaster` will later push that same transaction into the client's brand-new queue — the client sees it once from the REST snapshot and again from SSE, with no coordination between the two sources telling either side "this client already has it."

## What Changes

- Deduplicate by `transaction_id` on the client: track the set of transaction IDs already rendered (from the initial REST snapshot and from every SSE event since), and ignore any incoming SSE event whose ID is already known, instead of appending it to the feed again.
- **Alternative considered and rejected for this change**: give each SSE client its own starting cursor (e.g., the client sends the highest `seq` it received from the REST snapshot when opening `/stream`, and the server catches that client up from exactly that point instead of sharing one global `Broadcaster` cursor across all clients). This would fix the race at its root instead of masking the symptom, but it requires restructuring `Broadcaster` from one shared queue-fanout to a per-client cursor, a larger change to a component several other dashboard panels depend on. Client-side dedupe by ID is simple, sufficient to eliminate the visible symptom (a real transaction is never rendered twice), and doesn't touch the broadcaster's existing behavior for any other consumer.
- **BREAKING**: None — purely a frontend rendering fix; no API, schema, or protocol change.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `monitoring-dashboard`: `Live transaction feed` — make "no duplicate entries" an explicit part of the requirement, not just an implicit assumption.

**Note on spec state**: `monitoring-dashboard` currently exists only inside the not-yet-archived `build-fraud-detection-portfolio` change (58/59 tasks done; `openspec list --specs` reports no specs yet, since nothing has been archived to `openspec/specs/` in this repo). This proposal's delta is written against that change's `specs/monitoring-dashboard/spec.md` as the source of truth for the current requirement text. If `build-fraud-detection-portfolio` is archived before this change, `openspec validate` should be re-run against the now-canonical `openspec/specs/monitoring-dashboard/spec.md`; if it's archived after, this change's delta needs to be confirmed to still apply cleanly. Either way, this is a real sequencing dependency to flag, not an error in this proposal.

## Impact

- **Code**: `app/static/app.js` only (add ID-based dedupe to the SSE event handler feeding `state.feed`).
- **No backend, schema, or infrastructure change.**
