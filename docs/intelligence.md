# Fraud intelligence workspace

Start the project with `.venv/bin/python dev.py`, then scroll to **Fraud intelligence**.
Existing replay, screening, reviews, wallet exports and operations remain available.
The selected source also selects the analytics source. The evidence selector can
independently inspect retained sources even while a provider is offline. The workspace refreshes every
10 seconds. New evidence tables are created automatically in the configured SQLite
or PostgreSQL database; existing score tables are preserved.

## Features

1. **Trend analytics:** hourly observations, model-flagged rate, average DWTS,
   native transaction count and ETH volume. A flag is estimated risk ≥80%, not a
   verified fraud label. Native chain totals exclude token quantities and known failed
   transfers. Without receipts, successful transfer volume cannot be distinguished
   from attempted volume; the selector labels status-unknown events accordingly.
2. **Wallet graph:** every selected evidence source shows its observed wallets as
   searchable risk-colored nodes. Confirmed-chain mode additionally shows directed
   sender-to-recipient transfers with counts and ETH amounts in edge tooltips; other
   sources never fabricate relationships. The diagram displays 24 wallets per page,
   with address search, risk filtering, pagination, zoom and pan controls.
3. **Anomaly feed:** observations with probability or enabled anomaly score ≥80%,
   using the selected source. Historical replay stays labelled as historical.
4. **Explainable AI:** saved top XGBoost contributions for selected anomalies or
   replayed decisions, expressed in raw model log-odds. Contributions describe the
   explainer model, not a causal explanation or percentage-point decomposition of
   the calibrated probability.
5. **Cluster candidates:** connected components of at least two wallets whose
   latest observed risk is ≥80%, using actual transfer edges. This deterministic
   graph heuristic provides investigation leads, not confirmed collusion.
6. **Behavior profiles:** retained activity frequency, counterparties, gas used,
   failures and receipt coverage. Missing receipt data appears as unavailable.
7. **Investigation replay:** previous/next, playback and a timeline slider over
   saved decisions and chain transactions. These controls do not rescore wallets.
8. **Alerts:** persistent dashboard high-risk notices, acknowledgement, optional
   webhook and STARTTLS SMTP email. Successful channels are not resent when another
   channel fails. Delivery runs every 30 seconds, with at most three attempts and
   five alerts per pass; failures remain visible. Webhooks receive an Idempotency-Key.
   External delivery is at-least-once across crashes, so receivers should deduplicate.
9. **Monitoring:** mean/p95 observed latency, drift warning counts and five risk
   distribution bins. Live latency is block-processing time divided by sender count.
10. **What-if simulator:** choose a sender, compare original and proposed ETH amounts,
    and optionally change the recipient. The result keeps the raw wallet-model output
    visible and adds a transparent scenario-only adjustment: amount changes are bounded
    to ±12 percentage points and a recipient change adds 5 points. Both previews use
    the same confirmed-history lock and never save scores or transactions.

## Evidence scope

Analytics reads at most the latest 2,000 decisions for a source and 2,000 retained
chain events. Investigation loads up to 500 decisions and 500 events per wallet.
The UI identifies caps. Hourly decision and chain series each describe their own
bounded samples, not whole-chain totals. Old wallet snapshots have no transaction
counterparties; relationships are never fabricated. New explanations are saved
from this version onward; old score history remains accessible in the original view.
Decisions record the score as observed at the time. A chain rollback removes orphaned
block evidence and dashboard alerts; previously delivered external messages cannot
be recalled. Existing chain retention applies to graph and behavior-profile evidence.

## Configuration

Confirmed-chain features require the existing `WEB3_PROVIDER_URL` configuration.
Set `LIVE_INCLUDE_RECEIPTS=true` to collect receipt status and gas used. Pending
anomalies use the existing WebSocket provider configuration. External alert delivery
requires `ALERT_WEBHOOK_URL` and/or all of `ALERT_SMTP_HOST`, `ALERT_EMAIL_FROM`,
`ALERT_EMAIL_TO`. Optional SMTP values: port (587 default), user and password.
Credentials belong in the server environment, never the frontend. No delivery
endpoint is enabled by default. Once enabled, saved pending alerts can be delivered.
Only high-risk observations use the new persistent email/webhook delivery worker;
existing ingestion-error and drift webhook notifications retain their original path.

When role keys are configured, simulation requires viewer or higher and alert
acknowledgement requires reviewer or higher. Read-only intelligence views follow
the existing public read policy. With no keys, local demo actions remain open.

## Verification

Run `.venv/bin/python -m pytest -q backend/tests` and
`npm --prefix frontend run build`. The new tests cover source separation, token and
failed-transfer exclusion, clusters, profiles, idempotency, rollback, alert delivery
and retries (mocked external services), validation, role enforcement, confirmed-block
integration and simulation without state changes.
