# Dashboard verification

The light interface uses one shared spacing and color system in `frontend/src/index.css`.
Desktop panel pairs have equal-width columns and stretch to a shared row height. At 850px
and below, panels stack in reading order. Controls remain keyboard-accessible and motion
respects the operating-system preference.

## Automated checks

```sh
.venv/bin/python -m pytest backend/tests -q
npm --prefix frontend test
npm --prefix frontend run build
cd frontend
npx playwright install chromium
npm run test:e2e
```

The fourteen Playwright regression cases use isolated, deterministic API fixtures; no real
provider, wallet database, API keys, email, or webhook is needed. The suite starts its own
Vite server on port 5175 and fails if that port is occupied. It checks widths of 320, 390,
768, 1024 and 1440px, panel alignment, graph filters/zoom/pagination, history navigation,
screening-result invalidation and readable errors. Backend tests separately check the real
history query beyond 500 records, duplicate timestamps, transaction/decision merging,
snapshot cutoff, invalid pagination inputs, and empty history.

## History and graph behavior

- Wallet scores have 12-item pages; CSV export still includes the full score history.
- Investigation history has 50-item server-side pages, newest page first. Replay runs
  chronologically within the selected page. Paging preserves a timestamp cutoff so newly
  arriving current-time observations do not shift existing pages. Chain reorganizations,
  retention cleanup, or newly imported backdated events can still change retained history;
  reopen the investigation to refresh the snapshot.
- Behavior statistics use the latest 500 retained chain events and say so in the UI.
- Graph search runs when **Search graph** is pressed or Enter is used. Search and risk
  filtering apply before its 24-wallet pagination. Every source exposes observed wallets
  as nodes; only confirmed-chain evidence creates transfer edges. Edges connect wallets
  visible on the current page. Reset view restores the initial zoom and position.
- Graph/overview analytics still use a bounded 2,000-record sample; pagination does not
  claim to reconstruct chain data that was never ingested or has been pruned.

Production builds split chart libraries, framework dependencies, the main workspace,
and the lazy-loaded intelligence tools into separate cacheable chunks.
