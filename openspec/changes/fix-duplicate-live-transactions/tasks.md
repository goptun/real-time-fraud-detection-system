## 1. Client-side deduplication

- [ ] 1.1 In `app/static/app.js`, track the set of `transaction_id` values already in `state.feed` (seed it from the initial `transactions?limit=200` response); verify by inspecting the tracked set after initial load matches the snapshot's IDs.
- [ ] 1.2 In the SSE message handler that appends incoming transactions to `state.feed`, skip and discard any event whose `transaction_id` is already in the tracked set instead of rendering it again; verify with a test/manual repro that replays the same transaction ID twice through the handler and confirms only one entry renders.
- [ ] 1.3 Ensure the tracked-ID set doesn't grow unbounded over a long-lived session (e.g., cap it or align its retention with the feed's own rendered-item limit); verify memory/collection size stays bounded during an extended manual test.

## 2. Verification

- [ ] 2.1 Manually reproduce the original race: open the dashboard while transactions are actively streaming, and confirm no transaction ID appears twice in the rendered feed across several minutes of observation.
- [ ] 2.2 Confirm the existing "New transactions appear without reload" behavior is unaffected — new, genuinely distinct transactions still appear live.
- [ ] 2.3 Re-run this change's `openspec validate --strict`; if `build-fraud-detection-portfolio` has been archived in the meantime, re-verify this change's delta still applies cleanly against the now-canonical `openspec/specs/monitoring-dashboard/spec.md` (see proposal.md's note on spec state).
