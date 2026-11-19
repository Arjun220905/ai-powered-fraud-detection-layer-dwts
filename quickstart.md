# DWTS Fraud Detection - Simple Quick Start

This guide explains what the project does, how to start it, and what to enter in the
dashboard. You do not need blockchain or machine-learning experience.

## 1. What the project does

The project watches Ethereum wallet activity and estimates how risky it looks.

It shows:

- **Fraud probability:** the model's estimate from 0% to 100%.
- **Wallet trust score:** a running score from 0 to 100. Higher is safer.
- **Decision:** Observe, Approve, Delay, Verify, or Block.
- **Explanation:** the wallet behaviours that influenced the prediction.

It does **not** move money, freeze wallets, or stop transactions already sent to
Ethereum. Its decisions are recommendations.

## 2. The three dashboard modes

### Simulated

Replays 9,841 labelled historical wallet records from the Kaggle dataset. Use this
mode for a dependable college demonstration without needing blockchain access.

### Pending

Shows transactions that the configured Alchemy provider can see before they are
confirmed. These results are advisory and do not change the wallet's stored trust
score.

### Live

Reads confirmed Ethereum blocks, stores wallet behaviour, and updates DWTS after
enough observations have been collected.

## 3. What is a Web3 provider URL?

Your computer does not contain a complete Ethereum node. A Web3 provider such as
Alchemy gives the project a secure internet address for talking to one.

There are two URL types:

| URL | Starts with | Used for |
|---|---|---|
| HTTP provider | `https://` | Reading confirmed blocks and transaction details |
| WebSocket provider | `wss://` | Keeping a live connection open for pending transactions |

Example format only:

```text
https://eth-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY
wss://eth-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY
```

`wss` means **secure WebSocket**. It lets Alchemy push new pending transactions to
the project immediately instead of waiting for the project to ask repeatedly.

## 4. How to get the Alchemy URLs

1. Open [Alchemy](https://dashboard.alchemy.com/) and create or sign in to an account.
2. Open **Apps**. A new account may already have a default app.
3. Choose **Create new app** if you need a separate project.
4. Give it a name such as `DWTS Fraud Detection`.
5. Enable the **Ethereum** chain and the node/JSON-RPC service.
6. Open the app and select the **Endpoints** tab.
7. Find Ethereum Mainnet and copy its **HTTP** endpoint.
8. The same page also provides the **WebSocket** endpoint.

Alchemy's current official instructions are available in
[Create an Alchemy API key](https://www.alchemy.com/docs/create-an-api-key) and
[Pending transactions via WebSockets](https://www.alchemy.com/docs/how-to-subscribe-to-transactions-via-websocket-endpoints).

If you cannot see a network selector, open the app's chain configuration, enable
Ethereum, and then return to **Endpoints**. Use the endpoint labelled Ethereum
Mainnet - not an old test network.

## 5. Add the provider to this project

Open `.env` in the project root and set:

```dotenv
WEB3_PROVIDER_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY
WEB3_WS_PROVIDER_URL=
WEB3_WS_PROVIDER_URLS=
WEB3_PENDING_SUBSCRIPTION=alchemy_pendingTransactions
LIVE_RETENTION_BLOCKS=256
```

For Alchemy, leaving `WEB3_WS_PROVIDER_URL` blank is intentional. The backend safely
changes the configured Alchemy `https://` address into the matching `wss://` address.
If you have multiple WebSocket providers, put their comma-separated endpoints in
`WEB3_WS_PROVIDER_URLS`; the backend will fail over automatically.

You may instead paste the WebSocket endpoint explicitly:

```dotenv
WEB3_WS_PROVIDER_URL=wss://eth-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY
```

Never commit `.env`, publish screenshots containing these URLs, or send the key to
someone else. If it is exposed, create a replacement key in Alchemy.

## 6. Alchemy key versus dashboard API key

These are different:

- `WEB3_PROVIDER_URL` contains the **Alchemy key** used to read Ethereum.
- `VIEWER_API_KEY` lets you screen and simulate. `API_KEY` is the fallback admin key.

After opening the dashboard, paste the value of your configured role key into
**Operations and access**. Use a viewer key for screening. The browser keeps the
key in the current tab's session.
The interface is labelled **DWTS** and is organized as a decision console: start a
source at the top, inspect its event ledger and model evidence, then use screening or
wallet casework below. Pending and confirmed-chain buttons remain disabled until their
matching provider is configured.

## 7. Install the project for the first time

Prerequisites: Python 3.10–3.13, Node.js 20.19.x or 22.12+, and npm 10+.
On macOS, install the XGBoost runtime once with `brew install libomp`.

Windows PowerShell:

```powershell
cd ai-powered-fraud-detection-layer-dwts
py -3 dev.py --check
```

macOS/Linux:

```bash
cd ai-powered-fraud-detection-layer-dwts
python3 dev.py --check
```

The check command creates `.venv`, installs the pinned backend and frontend packages,
and trains the local model because binary model files are not committed.

## 8. Start everything with one command

Run `py -3 dev.py` on Windows or `python3 dev.py` on macOS/Linux. The launcher
automatically uses the project's virtual environment and skips setup work that is
already current. Running `python3 dev.py` from inside `frontend/` also works and
forwards to the same project launcher.

Open the **Frontend** URL printed after **Ready**. The dashboard uses port 5173 by
default and tries 5174 if 5173 is occupied. The backend defaults to port 8000.
Open [backend health](http://127.0.0.1:8000/health) or
[API documentation](http://127.0.0.1:8000/docs) using the backend port printed by
your launcher if you changed it.

Press `Ctrl+C` in the terminal to stop both services cleanly.

## 9. How to demonstrate the dashboard

### Historical demonstration

1. Choose **Dataset replay**.
2. Click **Start Dataset replay**.
3. Explain the fraud probability and simulated trust score. Replay records
   demo observations separately from operational wallet scores.

### Pending demonstration

1. Configure Alchemy in `.env`.
2. Restart `python dev.py` after changing `.env`.
3. Choose **Pending watch** and click **Start Pending watch**.
4. Explain that these transactions are visible to Alchemy but are not confirmed yet.

### Confirmed blockchain demonstration

1. Set an Ethereum Mainnet HTTPS provider in `.env` and restart the launcher.
2. Choose **Confirmed chain**.
3. Click **Start Confirmed chain**.
4. Read **Confirmed chain health** for the provider head, last processed block,
   its age, and blocks behind the confirmed head. If ingestion reports an error,
   the panel flags the cached feed as potentially stale.

### Pre-broadcast screening

This checks a transaction before an application broadcasts it.

1. In **Operations and access**, enter a viewer key if access protection is enabled.
   Use the local `API_KEY` only if that is the credential you configured.
2. Enter the sender wallet address.
3. Enter the destination wallet or contract address.
4. Enter the ETH amount.
5. Use gas limit `21000` for a normal ETH transfer. Contract calls need the gas limit
   estimated by the wallet.
6. Click **Screen transaction**.

Safe example values:

```text
Sender:    0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
Recipient: 0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
ETH value: 0.01
Gas limit: 21000
```

Screening does not send the transaction and does not change DWTS.

### Wallet transaction graph

Scroll to **Fraud intelligence**. Dataset replay shows observed wallets as nodes even
though the historical dataset has no trustworthy sender-to-recipient transactions.
Choose **Retained confirmed chain** to see real transfer arrows. Enter a full or partial
address under **Find wallet**, press **Search graph** (or Enter), then select a node to
open its investigation. Risk filtering, pagination, zoom and pan remain available.

### Demonstrate the Case desk

1. Start **Dataset replay** and pause when an assessment appears.
2. Click **Save current assessment**. In **Case desk**, give the wallet a label,
   choose **Research sample**, add a review note, and save.
3. Open the wallet's history and show the saved assessment's source, model
   version, and explanation. Save another assessment of the same wallet to
   compare them. A numeric delta requires a matching source and model version.
4. Click **Export case JSON** to save the notes and snapshots as evidence for
   your project report. This demo works without a live Ethereum provider.

For the live demo, open **Trace wallet connections** in Intelligence, select
retained confirmed-chain data, then enter two addresses from the retained
graph or select **Try a retained pair**. The search follows sender-to-recipient
edges for up to four hops. Use
**Swap wallets** to demonstrate why direction matters.

### What-if risk comparison

Enter a sender, an original ETH amount, and a different test amount. You can optionally
compare a recipient change. Press **Compare risk**. The result separates the raw
wallet-model probability from the bounded scenario adjustment and never sends or saves
a transaction.

## 10. Understanding the result

DWTS starts at 70 and gradually changes as confirmed observations arrive.

| Trust score | Recommendation | Plain meaning |
|---:|---|---|
| 75-100 | Approve | Low observed risk |
| 55-74.99 | Delay | Wait or collect more information |
| 30-54.99 | Verify | Perform manual checks |
| Below 30 | Block | Do not proceed without investigation |

New live wallets usually show **Observe** until at least 10 events have been seen.
This prevents the system from making a strong decision using too little history.

## 11. How the technology flows

```text
Dataset or Ethereum provider
        |
Wallet behaviour features
        |
XGBoost fraud probability
        |
Dynamic Wallet Trust Score
        |
Observe / Approve / Delay / Verify / Block
        |
FastAPI sends the result to the React dashboard
```

SQLite stores scores, blocks, pending status, reviews, rate limits, and audit records.
PostgreSQL can replace SQLite for a deployed multi-user system.

## 12. What the model result really means

The reported 98.42% accuracy was measured on a wallet-group-separated held-out part of
the historical Kaggle dataset. It is not proof of 98.42% accuracy on every new Mainnet
transaction.
Live decisions remain marked experimental until enough genuine human-reviewed live
outcomes are collected and separately validated.

## 13. Common problems

### `No module named dotenv`

Use the current `dev.py`; it creates and uses `.venv` automatically. If installation
was interrupted, rerun `python dev.py --check`.

### A launcher port is already in use

For a previous DWTS instance, press `Ctrl+C` in the terminal that started it.
For a different application, leave it running and choose a free port. For example,
on macOS/Linux run `DWTS_BACKEND_PORT=18000 DWTS_FRONTEND_PORT=15173 python3 dev.py`.
You can also set those two variables in the project's `.env` before launching.

### Provider shows disconnected

- Confirm the URL begins with `https://`.
- Confirm Ethereum is enabled for the Alchemy app.
- Confirm the key has not been deleted, restricted incorrectly, or copied with spaces.
- Restart the project after editing `.env`.

### Pending mode is empty

- Confirm the HTTP endpoint belongs to Alchemy, or set the WSS endpoint explicitly.
- Confirm the WSS URL begins with `wss://`.
- Wait briefly; pending traffic is not constant.
- Check **Operations and access** for a provider error.

### `401` or `Valid X-API-Key required`

Enter the value of your configured key in **Operations and access**. A viewer key
can screen transactions and run simulations. Reviewer and admin tasks require the
corresponding role. `API_KEY` is the fallback admin credential.

### Live mode is catching up

Alchemy reports the current Mainnet head. The last processed block shows what your
local worker has saved. Short gaps are processed in order. With the default settings,
a gap greater than 256 blocks starts a new segment near the confirmed head so the
live view recovers quickly. Open **Block details** to see the skipped range. Set
`LIVE_RESUME_MAX_GAP=0` in `.env` before launching if you need contiguous coverage.

Choose **Color theme** in the top bar to switch between Indigo light and Indigo dark.
Your browser remembers the choice.

## 14. Run the checks

```text
python dev.py --check
.venv/bin/python -m pytest -q backend/tests
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:e2e
npm --prefix frontend run test:stack
```

On Windows, use `.\.venv\Scripts\python.exe` instead of `.venv/bin/python`.
Install Chromium with `cd frontend && npx playwright install chromium` before running
the browser checks. Run the real-stack test on macOS or Linux. It uses a temporary
database and test ports 15173 and 18000.

## 15. Honest limitations

- A provider cannot guarantee visibility into the entire global Ethereum mempool.
- The project cannot stop transactions submitted through unrelated wallets or apps.
- Pending decisions are advisory; confirmed observations are authoritative for DWTS.
- Real live-model improvement requires genuine reviewed outcomes.
- A public deployment still needs hosted PostgreSQL, TLS, monitoring, backups, and
  controlled user accounts.

For technical details, see [README.md](README.md) and
[docs/architecture.md](docs/architecture.md).
