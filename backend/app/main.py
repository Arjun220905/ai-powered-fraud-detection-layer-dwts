from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import secrets
import time
import uuid
from urllib.error import URLError
from urllib.request import Request as UrlRequest, urlopen
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import joblib
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

from app.blockchain.web3_listener import (
    block_transactions,
    current_block_number,
    latest_block_transactions,
    provider_configured,
    provider_status,
    transaction_by_hash,
    decode_contract_call,
)
from app.blockchain.pending_listener import (
    pending_provider_configured, pending_provider_urls, pending_transactions,
)
from app.blockchain.live_features import LiveWalletFeatures
from app.ml.feature_engineering import row_to_frame, rows_to_frame
from app.models import (
    PredictionRequest, PredictionResponse, ReviewedLabelRequest,
    TransactionScreenRequest, SimulationRequest,
)
from app.stream.simulator import StreamSimulator
from app.trust_score.db import TrustStore
from app.trust_score.scorer import risk_action, update_score
from app import intelligence

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def configured_path(name: str, default: Path) -> Path:
    value = Path(os.getenv(name, str(default)))
    return value if value.is_absolute() else ROOT / value


MODEL_PATH = configured_path("MODEL_PATH", ROOT / "backend/app/ml/model.pkl")
DATASET_PATH = configured_path("DATASET_PATH", ROOT / "data/transaction_dataset.csv")
DATABASE_PATH = configured_path("DATABASE_PATH", ROOT / "backend/wallet_scores.db")
DATABASE_LOCATION = os.getenv("DATABASE_URL", "").strip() or str(DATABASE_PATH)
logger = logging.getLogger("dwts")
ROLE_LEVEL = {"viewer": 1, "reviewer": 2, "admin": 3}


def positive_int(name: str, default: int, minimum: int = 1) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def configured_api_keys() -> dict[str, str]:
    admin = os.getenv("ADMIN_API_KEY", "").strip() or os.getenv("API_KEY", "").strip()
    return {
        role: value for role, value in {
            "viewer": os.getenv("VIEWER_API_KEY", "").strip(),
            "reviewer": os.getenv("REVIEWER_API_KEY", "").strip(),
            "admin": admin,
        }.items() if value
    }


def request_role(request: Request) -> str | None:
    supplied = request.headers.get("X-API-Key", "")
    for role, configured in configured_api_keys().items():
        if secrets.compare_digest(supplied, configured):
            return role
    return None


def required_role(path: str, method: str) -> str | None:
    if method == "OPTIONS":
        return None
    if path.startswith("/api/operations") or path == "/metrics":
        return "admin"
    if path.startswith("/api/labels"):
        return "reviewer"
    if path in {"/api/screen-transaction", "/api/intelligence/simulate"}:
        return "viewer"
    if path.startswith("/api/") and method not in {"GET", "HEAD", "OPTIONS"}:
        return "reviewer"
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []
    try:
        if not MODEL_PATH.is_file():
            raise RuntimeError(f"Model not found at {MODEL_PATH}; run train_model.py first")
        artifact = joblib.load(MODEL_PATH)
        app.state.model = artifact["pipeline"]
        app.state.explain_model = artifact.get("explain_pipeline")
        app.state.anomaly_model = artifact.get("anomaly_pipeline")
        app.state.anomaly_reference = artifact.get("anomaly_reference")
        app.state.model_version = artifact.get("model_version", "legacy")
        app.state.features = artifact["features"]
        app.state.store = TrustStore(DATABASE_LOCATION)
        app.state.worker_id = str(uuid.uuid4())
        app.state.live_leader = False
        app.state.stream = StreamSimulator(str(DATASET_PATH))
        # Large confirmed-chain histories must not block the independent CSV replay.
        app.state.live_features = LiveWalletFeatures()
        app.state.live_features_ready = False
        app.state.live_cache = app.state.store.metadata("live_cache")
        app.state.live_error = None
        app.state.pending_error = None
        app.state.last_alert = None
        app.state.live_lock = asyncio.Lock()
        app.state.live_poll_seconds = positive_int("LIVE_POLL_SECONDS", 6)
        app.state.live_lease_seconds = max(60, app.state.live_poll_seconds * 5)
        app.state.live_confirmations = positive_int("LIVE_CONFIRMATIONS", 2, 0)
        app.state.live_backfill_blocks = positive_int("LIVE_BACKFILL_BLOCKS", 5)
        app.state.live_max_catchup_blocks = positive_int("LIVE_MAX_CATCHUP_BLOCKS", 100)
        app.state.live_catchup_concurrency = min(
            positive_int("LIVE_CATCHUP_CONCURRENCY", 4), 16
        )
        app.state.live_min_wallet_events = positive_int("LIVE_MIN_WALLET_EVENTS", 10)
        app.state.live_display_limit = positive_int("LIVE_DISPLAY_LIMIT", 25)
        app.state.live_retention_blocks = positive_int("LIVE_RETENTION_BLOCKS", 256)
        app.state.live_prune_every = positive_int("LIVE_PRUNE_EVERY_BLOCKS", 100)
        app.state.pending_ttl_seconds = positive_int("PENDING_TTL_SECONDS", 300)
        app.state.pending_retention_seconds = positive_int(
            "PENDING_RETENTION_SECONDS", 86400
        )
        app.state.pending_max_records = positive_int("PENDING_MAX_RECORDS", 10000)
        app.state.pending_processed = 0
        app.state.rate_limit_per_minute = positive_int("RATE_LIMIT_PER_MINUTE", 120)
        app.state.live_processed_blocks = 0
        app.state.drift_reference = artifact.get("feature_reference", {})
        app.state.drift_threshold = float(os.getenv("LIVE_DRIFT_Z_THRESHOLD", "3"))
        if not math.isfinite(app.state.drift_threshold) or app.state.drift_threshold <= 0:
            raise ValueError("LIVE_DRIFT_Z_THRESHOLD must be a positive finite number")
        short_keys = [
            name for name in (
                "API_KEY", "VIEWER_API_KEY", "REVIEWER_API_KEY", "ADMIN_API_KEY"
            ) if os.getenv(name) and len(os.getenv(name, "")) < 16
        ]
        if short_keys:
            raise ValueError(f"{', '.join(short_keys)} must contain at least 16 characters")
        app.state.store.expire_pending(app.state.pending_ttl_seconds)
        app.state.store.prune_pending(
            app.state.pending_retention_seconds, app.state.pending_max_records
        )
        if provider_configured() or pending_provider_configured():
            tasks.append(asyncio.create_task(start_live_services(app)))
        else:
            app.state.live_features_ready = True
        tasks.append(asyncio.create_task(intelligence.alert_worker(app.state)))
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        raise RuntimeError(f"Startup failed: {exc}") from exc
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        if hasattr(app.state, "store"):
            app.state.store.close()


app = FastAPI(title="Fraud Detection DWTS API", version="1.0.0", lifespan=lifespan)
origins = [item.strip() for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if item.strip()]


async def start_live_services(app: FastAPI) -> None:
    """Recover live state in the background so historical replay starts immediately."""
    workers = []
    try:
        app.state.live_features = await run_in_threadpool(
            rebuild_live_features, app.state.store
        )
        app.state.live_features_ready = True
        if provider_configured():
            workers.append(asyncio.create_task(live_poller(app)))
        if pending_provider_configured():
            workers.append(asyncio.create_task(pending_poller(app)))
        if workers:
            await asyncio.gather(*workers)
    except asyncio.CancelledError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        app.state.live_error = f"Live-state recovery failed: {exc}"
        logger.error("%s", app.state.live_error)
    finally:
        for worker in workers:
            worker.cancel()
        for worker in workers:
            with suppress(asyncio.CancelledError):
                await worker

@app.middleware("http")
async def security_rate_limit_and_audit(request: Request, call_next):
    started = time.monotonic()
    client_ip = request.client.host if request.client else "unknown"
    limit = getattr(request.app.state, "rate_limit_per_minute", 120)
    minimum_role = required_role(request.url.path, request.method)
    response = None
    try:
        if configured_api_keys() and minimum_role:
            role = request_role(request)
            if role is None:
                response = JSONResponse({"detail": "Valid X-API-Key required. Add a viewer key under Operations and access."}, status_code=401)
            elif ROLE_LEVEL[role] < ROLE_LEVEL[minimum_role]:
                response = JSONResponse(
                    {"detail": f"{minimum_role} role required"}, status_code=403
                )
        if (
            response is None
            and request.method != "OPTIONS"
            and hasattr(request.app.state, "store")
            and not await run_in_threadpool(
                request.app.state.store.allow_request, client_ip, limit
            )
        ):
            response = JSONResponse(
                {"detail": "Rate limit exceeded; retry in one minute"}, status_code=429
            )
        elif response is None:
            response = await call_next(request)
        return response
    finally:
        if response is not None and hasattr(request.app.state, "store"):
            duration = (time.monotonic() - started) * 1000
            try:
                await run_in_threadpool(
                    request.app.state.store.audit,
                    request.method, request.url.path, response.status_code, client_ip, duration,
                )
            except RuntimeError as exc:
                logger.error("Audit write failed: %s", exc)


# Register CORS after the security middleware so authentication and rate-limit
# responses also receive browser-readable CORS headers.
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])


def predict_probability(state, values: dict) -> float:
    frame = row_to_frame(values, state.features)
    return float(state.model.predict_proba(frame)[0, 1])


def anomaly_score(state, values: dict) -> float | None:
    if not state.anomaly_model or not state.anomaly_reference:
        return None
    raw = float(state.anomaly_model.score_samples(row_to_frame(values, state.features))[0])
    low, high = state.anomaly_reference["low"], state.anomaly_reference["high"]
    return round(max(0.0, min(1.0, (high - raw) / (high - low))), 4) if high > low else None


def explain_prediction(state, values: dict, limit: int = 5) -> list[dict]:
    if not state.explain_model:
        return []
    from xgboost import DMatrix

    frame = row_to_frame(values, state.features)
    transformed = state.explain_model.named_steps["imputer"].transform(frame)
    contributions = state.explain_model.named_steps["classifier"].get_booster().predict(
        DMatrix(transformed), pred_contribs=True
    )[0][:-1]
    ranked = sorted(
        zip(state.features, contributions), key=lambda item: abs(item[1]), reverse=True
    )[:limit]
    return [
        {
            "feature": name,
            "impact": round(float(value), 4),
            "direction": "higher risk" if value > 0 else "lower risk",
        }
        for name, value in ranked
    ]


def drift_score(state, values: dict) -> float | None:
    scores = []
    for name, reference in state.drift_reference.items():
        value = values.get(name)
        deviation = reference.get("std", 0)
        if value is not None and deviation and math.isfinite(float(value)):
            scores.append(min(abs((float(value) - reference["mean"]) / deviation), 10))
    return round(sum(scores) / len(scores), 3) if scores else None


def send_alert(state, kind: str, message: str) -> bool:
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    fingerprint = f"{kind}:{message}"
    if not url or state.last_alert == fingerprint:
        return False
    state.last_alert = fingerprint
    try:
        request = UrlRequest(
            url,
            data=json.dumps({"service": "dwts", "kind": kind, "message": message}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            if not 200 <= response.status < 300:
                raise URLError(f"webhook returned HTTP {response.status}")
        return True
    except (OSError, URLError, ValueError) as exc:
        logger.error("Alert delivery failed: %s", exc)
        return False


def infer_and_update(request: Request, address: str, values: dict, source: str = "api") -> tuple:
    started = time.perf_counter()
    try:
        probability = predict_probability(request.app.state, values)
        current = request.app.state.store.current_score(address)
        result = update_score(current, probability)
        request.app.state.store.save(address, result.score, probability, result.action)
        explanation = explain_prediction(request.app.state, values)
        latency = round((time.perf_counter() - started) * 1000, 3)
        anomaly = anomaly_score(request.app.state, values)
        drift = drift_score(request.app.state, values)
        intelligence.record(request.app.state, source, address, dict(
            fraud_probability=round(probability, 4), trust_score=result.score,
            risk_action=result.action, explanation=explanation, latency_ms=latency,
            anomaly_score=anomaly, drift_score=drift,
            drift_warning=drift is not None and drift >= request.app.state.drift_threshold,
            model_version=request.app.state.model_version))
        return (
            round(probability, 4), result.score, result.action,
            explanation, latency, anomaly,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {exc}") from exc


def rebuild_live_features(store: TrustStore) -> LiveWalletFeatures:
    features = LiveWalletFeatures()
    for event in store.events():
        if event["kind"] == "native":
            features.observe({
                "from": event["sender"],
                "to": event["recipient"],
                "value_eth": event["value"],
                "gas": event.get("gas"),
                "gas_used": event.get("gas_used"),
                "gas_price_wei": event.get("gas_price_wei"),
                "nonce": event.get("nonce"),
                "input_selector": event.get("input_selector"),
                "status": event.get("status"),
            }, event["timestamp"])
        else:
            features.observe_erc20({
                "from": event["sender"],
                "to": event["recipient"],
                "token_address": event["token_address"],
                "value": event["value"],
            }, event["timestamp"])
    return features


def persisted_events(block: dict) -> list[dict]:
    events = [{
        "block_number": block["number"],
        "transaction_hash": transaction["hash"],
        "event_index": index,
        "kind": "native",
        "sender": transaction["from"].lower(),
        "recipient": transaction["to"].lower() if transaction["to"] else None,
        "value": transaction["value_eth"],
        "token_address": None,
        "gas": transaction.get("gas"),
        "gas_used": transaction.get("gas_used"),
        "gas_price_wei": transaction.get("gas_price_wei"),
        "nonce": transaction.get("nonce"),
        "input_selector": transaction.get("input_selector"),
        "contract_call": json.dumps(transaction.get("contract_call"))
        if transaction.get("contract_call") else None,
        "status": transaction.get("status"),
    } for index, transaction in enumerate(block["transactions"])]
    events.extend({
        "block_number": block["number"],
        "transaction_hash": transfer["transaction_hash"],
        "event_index": transfer["log_index"],
        "kind": "erc20",
        "sender": transfer["from"].lower(),
        "recipient": transfer["to"].lower(),
        "value": transfer["value"],
        "token_address": transfer["token_address"].lower(),
        "gas": None, "gas_used": None, "gas_price_wei": None, "nonce": None,
        "input_selector": None, "contract_call": None, "status": None,
    } for transfer in block["erc20_transfers"])
    return events


def bounded_catchup_head(first: int, confirmed_head: int, limit: int) -> int:
    return min(confirmed_head, first + limit - 1)


def process_live_block(app: FastAPI, block: dict) -> dict:
    started = time.perf_counter()
    app.state.store.save_block(block, persisted_events(block))
    app.state.store.reconcile_pending(
        [transaction["hash"] for transaction in block["transactions"]], block["number"]
    )
    senders = []
    seen_senders = set()
    for transaction in block["transactions"]:
        sender = app.state.live_features.observe(transaction, block["timestamp"])
        if sender not in seen_senders:
            seen_senders.add(sender)
            senders.append(sender)
    for transfer in block["erc20_transfers"]:
        app.state.live_features.observe_erc20(transfer, block["timestamp"])

    predictions = {}
    values_by_address = {
        address: app.state.live_features.features(address)
        for address in senders
    }
    probabilities = (
        app.state.model.predict_proba(
            rows_to_frame(list(values_by_address.values()), app.state.features)
        )[:, 1]
        if senders else []
    )
    current_scores = app.state.store.current_scores(senders)
    score_rows = []
    for address, probability in zip(senders, probabilities):
        values = values_by_address[address]
        probability = float(probability)
        current = current_scores[address]
        event_count = app.state.live_features.event_count(address)
        mature = event_count >= app.state.live_min_wallet_events
        result = update_score(current, probability) if mature else None
        score = result.score if result else current
        action = result.action if result else "Observe"
        score_rows.append({
            "address": address,
            "score": score,
            "probability": probability,
            "action": action,
            "source_key": f"live:{block['hash']}:{address}",
            "block_number": block["number"],
            "applied": int(mature),
        })
        predictions[address] = {
            "fraud_probability": round(probability, 4),
            "trust_score": score,
            "risk_action": action,
            "confidence": "experimental" if mature else "cold_start",
            "observed_events": event_count,
            "drift_score": drift_score(app.state, values),
            "features": {name: values.get(name) for name in app.state.features[:5]},
            "advanced_features": app.state.live_features.advanced_features(address),
            "anomaly_score": anomaly_score(app.state, values),
            "explanation": explain_prediction(app.state, values),
            "model_version": app.state.model_version,
        }
        predictions[address]["drift_warning"] = (
            predictions[address]["drift_score"] is not None
            and predictions[address]["drift_score"] >= app.state.drift_threshold
        )
    app.state.store.save_many(score_rows)
    elapsed = (time.perf_counter() - started) * 1000 / max(1, len(senders))
    for address, assessment in predictions.items():
        intelligence.record(app.state, 'live', address, assessment,
                            key=f"live:{block['hash']}:{address}", block_number=block['number'],
                            timestamp=block['timestamp'], latency_ms=elapsed)

    displayed = block["transactions"][-app.state.live_display_limit:]
    items = []
    offset = len(block["transactions"]) - len(displayed)
    for index, transaction in enumerate(displayed, start=offset):
        address = transaction["from"].lower()
        items.append({
            "sequence": f'{block["number"]}-{index}',
            "block_number": block["number"],
            "address": address,
            "transaction_hash": transaction["hash"],
            "value_eth": transaction["value_eth"],
            "gas": transaction["gas"],
            "recipient": transaction.get("to"),
            "gas_price_wei": transaction.get("gas_price_wei"),
            "nonce": transaction.get("nonce"),
            "input": transaction.get("input", "0x"),
            "contract_call": transaction.get("contract_call"),
            **predictions[address],
            "source": "Confirmed Ethereum block",
        })
    payload = {
        "mode": "live",
        "new_block": True,
        "block_number": block["number"],
        "block_hash": block["hash"],
        "block_timestamp": block["timestamp"],
        "block_transaction_count": block["transaction_count"],
        "erc20_transfer_count": len(block["erc20_transfers"]),
        "transactions": items,
    }
    app.state.store.save_metadata("live_cache", payload)
    app.state.store.mark_block_processed(block["number"])
    app.state.live_cache = payload
    drift_warnings = sum(bool(value["drift_warning"]) for value in predictions.values())
    if drift_warnings:
        send_alert(
            app.state, "feature_drift",
            f"{drift_warnings} wallets exceeded drift threshold in block {block['number']}",
        )
    app.state.live_processed_blocks += 1
    if app.state.live_processed_blocks % app.state.live_prune_every == 0:
        if app.state.store.prune_chain(app.state.live_retention_blocks):
            app.state.live_features = rebuild_live_features(app.state.store)
    return payload


async def ingest_live_blocks(app: FastAPI) -> None:
    head = await run_in_threadpool(current_block_number)
    confirmed_head = max(0, head - app.state.live_confirmations)
    last = await run_in_threadpool(app.state.store.last_block)

    if last:
        if not last["processed"]:
            await run_in_threadpool(app.state.store.rollback_from, last["number"])
            app.state.live_features = await run_in_threadpool(
                rebuild_live_features, app.state.store
            )
            last = await run_in_threadpool(app.state.store.last_block)
    if last:
        canonical = await run_in_threadpool(block_transactions, last["number"], 1)
        if canonical["hash"] != last["hash"]:
            await run_in_threadpool(app.state.store.rollback_from, last["number"])
            app.state.live_features = await run_in_threadpool(
                rebuild_live_features, app.state.store
            )
            app.state.live_cache = None
            raise RuntimeError(f"Chain reorganization rolled back block {last['number']}")
        first = last["number"] + 1
    else:
        first = max(0, confirmed_head - app.state.live_backfill_blocks + 1)

    missing = confirmed_head - first + 1
    if missing <= 0:
        return
    confirmed_head = bounded_catchup_head(
        first, confirmed_head, app.state.live_max_catchup_blocks
    )

    for batch_start in range(
        first, confirmed_head + 1, app.state.live_catchup_concurrency
    ):
        if not await run_in_threadpool(
            app.state.store.acquire_lease,
            "ethereum_ingestion", app.state.worker_id, app.state.live_lease_seconds,
        ):
            raise RuntimeError("Ethereum ingestion leadership was lost")
        numbers = range(
            batch_start,
            min(batch_start + app.state.live_catchup_concurrency, confirmed_head + 1),
        )
        # Full Ethereum blocks can contain hundreds of token transfers. Fetching
        # several of those expensive RPC payloads at once can temporarily exhaust
        # a provider's rate limit and stall the whole catch-up at the same block.
        # Preserve ordered processing and retry an individual transient failure.
        blocks = []
        for number in numbers:
            for attempt in range(3):
                try:
                    blocks.append(
                        await run_in_threadpool(block_transactions, number, None)
                    )
                    break
                except ConnectionError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.5 * (attempt + 1))
        for block in blocks:
            previous = await run_in_threadpool(app.state.store.last_block)
            if previous and block["parent_hash"] != previous["hash"]:
                await run_in_threadpool(app.state.store.rollback_from, previous["number"])
                app.state.live_features = await run_in_threadpool(
                    rebuild_live_features, app.state.store
                )
                raise RuntimeError(f"Chain reorganization rolled back block {previous['number']}")
            await run_in_threadpool(process_live_block, app, block)


def _hex_int(value) -> int:
    return int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value or 0)


def normalize_pending(value: dict) -> dict | None:
    if not value.get("from") or not value.get("hash"):
        return None
    data = value.get("input", "0x")
    return {
        "hash": value["hash"].lower(),
        "from": value["from"].lower(),
        "to": value.get("to").lower() if value.get("to") else None,
        "value_eth": str(_hex_int(value.get("value")) / 10**18),
        "gas": _hex_int(value.get("gas")),
        "gas_price_wei": str(_hex_int(
            value.get("gasPrice") or value.get("maxFeePerGas")
        )),
        "nonce": _hex_int(value.get("nonce")),
        "input": data,
        "input_selector": data[:10] if len(data) >= 10 else data,
        "contract_call": decode_contract_call(data),
    }


def screen_transaction(state, transaction: dict) -> dict:
    started = time.perf_counter()
    values, event_count = state.live_features.preview(transaction, int(time.time()))
    probability = predict_probability(state, values)
    current = state.store.current_score(transaction["from"])
    mature = event_count >= state.live_min_wallet_events
    result = update_score(current, probability) if mature else None
    return {
        "fraud_probability": round(probability, 4),
        "projected_trust_score": result.score if result else current,
        "risk_action": result.action if result else "Observe",
        "confidence": "experimental" if mature else "cold_start",
        "observed_events": event_count,
        "explanation": explain_prediction(state, values),
        "model_version": state.model_version,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "applied": False,
        "anomaly_score": anomaly_score(state, values),
        "advanced_features": state.live_features.advanced_features(transaction["from"])
        if transaction["from"] in state.live_features.wallets else {},
        "model_scope": "wallet_behavior_preview",
    }


async def pending_poller(app: FastAPI) -> None:
    while True:
        stream = None
        try:
            if not await run_in_threadpool(
                app.state.store.acquire_lease,
                "ethereum_pending", app.state.worker_id, app.state.live_lease_seconds,
            ):
                await asyncio.sleep(5)
                continue
            stream = pending_transactions()
            async for raw in stream:
                if not await run_in_threadpool(
                    app.state.store.acquire_lease,
                    "ethereum_pending", app.state.worker_id, app.state.live_lease_seconds,
                ):
                    break
                transaction = (
                    await run_in_threadpool(transaction_by_hash, raw)
                    if isinstance(raw, str) else normalize_pending(raw)
                )
                if not transaction:
                    continue
                assessment = await run_in_threadpool(
                    screen_transaction, app.state, transaction
                )
                await run_in_threadpool(app.state.store.save_pending, {
                    "hash": transaction["hash"],
                    "sender": transaction["from"],
                    "recipient": transaction.get("to"),
                    "value_eth": transaction["value_eth"],
                    "gas": transaction["gas"],
                    "gas_price_wei": transaction.get("gas_price_wei"),
                    "nonce": transaction.get("nonce"),
                    "input": transaction.get("input", "0x"),
                    "input_selector": transaction.get("input_selector"),
                    "contract_call": transaction.get("contract_call"),
                    "fraud_probability": assessment["fraud_probability"],
                    "projected_score": assessment["projected_trust_score"],
                    "action": assessment["risk_action"],
                    "confidence": assessment["confidence"],
                    "observed_events": assessment["observed_events"],
                    "decision_ms": assessment["latency_ms"],
                    "model_version": assessment["model_version"],
                    "explanation": assessment["explanation"],
                    "status": "pending",
                })
                app.state.pending_processed += 1
                await run_in_threadpool(
                    intelligence.record, app.state, 'pending', transaction['from'], assessment,
                    key=f"pending:{transaction['hash']}", transaction_hash=transaction['hash'],
                    recipient=transaction.get('to'), value_eth=transaction['value_eth'])
                if app.state.pending_processed % 100 == 0:
                    await run_in_threadpool(
                        app.state.store.expire_pending, app.state.pending_ttl_seconds
                    )
                    await run_in_threadpool(
                        app.state.store.prune_pending,
                        app.state.pending_retention_seconds,
                        app.state.pending_max_records,
                    )
                app.state.pending_error = None
        except (ConnectionError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            app.state.pending_error = str(exc)
            await run_in_threadpool(
                send_alert, app.state, "pending_ingestion_error", app.state.pending_error
            )
        finally:
            if stream is not None:
                await stream.aclose()
        await asyncio.sleep(3)


async def live_poller(app: FastAPI) -> None:
    while True:
        try:
            app.state.live_leader = await run_in_threadpool(
                app.state.store.acquire_lease,
                "ethereum_ingestion", app.state.worker_id,
                app.state.live_lease_seconds,
            )
            if app.state.live_leader:
                async with app.state.live_lock:
                    await ingest_live_blocks(app)
                    app.state.live_error = None
        except (ConnectionError, OSError, RuntimeError, ValueError) as exc:
            app.state.live_error = str(exc)
            await run_in_threadpool(
                send_alert, app.state, "ingestion_error", app.state.live_error
            )
        await asyncio.sleep(app.state.live_poll_seconds)


@app.get("/health")
async def health(request: Request):
    return {
        "status": "ok",
        "model_loaded": hasattr(request.app.state, "model"),
        "database_backend": request.app.state.store.backend,
    }


@app.get("/ready")
async def ready(request: Request):
    status = await run_in_threadpool(provider_status)
    ready_now = hasattr(request.app.state, "model") and (
        not status["configured"] or status["connected"]
    )
    return JSONResponse(
        {
            "status": "ready" if ready_now else "not_ready",
            "model_loaded": hasattr(request.app.state, "model"),
            "database": request.app.state.store.backend,
            "provider": status,
            "live_features_ready": request.app.state.live_features_ready,
        },
        status_code=200 if ready_now else 503,
    )


@app.get("/api/blockchain/status")
async def blockchain_status(request: Request):
    status = await run_in_threadpool(provider_status)
    last = await run_in_threadpool(request.app.state.store.last_block)
    return {
        **status,
        "authentication_required": bool(configured_api_keys()),
        "ingestion": {
            "running": provider_configured(),
            "leader": request.app.state.live_leader,
            "features_ready": request.app.state.live_features_ready,
            "last_confirmed_block": last["number"] if last else None,
            "error": request.app.state.live_error,
        },
        "pending": {
            "configured": pending_provider_configured(),
            "provider_count": len(pending_provider_urls()),
            "error": request.app.state.pending_error,
        },
    }


@app.get("/api/blockchain/latest-block")
async def blockchain_latest_block(limit: int = Query(25, ge=1, le=100)):
    try:
        return await run_in_threadpool(latest_block_transactions, limit)
    except (ConnectionError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/blockchain/live-stream")
async def blockchain_live_stream(
    request: Request,
    limit: int = Query(25, ge=1, le=50),
    after_block: int | None = Query(None, ge=0),
):
    payload = request.app.state.live_cache
    if not payload:
        if request.app.state.live_error:
            raise HTTPException(status_code=503, detail=request.app.state.live_error)
        return {"mode": "live", "new_block": False, "block_number": None, "transactions": []}
    return {
        **payload,
        "new_block": after_block is None or payload["block_number"] > after_block,
        "transactions": payload["transactions"][-limit:],
        "ingestion_error": request.app.state.live_error,
    }


@app.get("/api/blockchain/pending-stream")
async def blockchain_pending_stream(
    request: Request, limit: int = Query(25, ge=1, le=100),
):
    await run_in_threadpool(
        request.app.state.store.expire_pending, request.app.state.pending_ttl_seconds
    )
    rows = await run_in_threadpool(request.app.state.store.pending, limit)
    return {
        "mode": "pending",
        "configured": pending_provider_configured(),
        "error": request.app.state.pending_error,
        "transactions": [{
            **row,
            "sequence": row["hash"],
            "address": row["sender"],
            "transaction_hash": row["hash"],
            "trust_score": row["projected_score"],
            "risk_action": row["action"],
            "latency_ms": row["decision_ms"],
            "source": "Provider-visible pending transaction",
        } for row in rows],
    }


@app.post("/api/screen-transaction")
async def screen_proposed_transaction(payload: TransactionScreenRequest, request: Request):
    if not request.app.state.live_features_ready:
        raise HTTPException(
            status_code=503,
            detail="Live wallet history is still loading; dataset replay remains available",
        )
    transaction = {
        "hash": f"screen:{uuid.uuid4()}",
        "from": payload.sender,
        "to": payload.recipient,
        "value_eth": str(payload.value_eth),
        "gas": payload.gas,
        "gas_price_wei": str(payload.gas_price_wei or 0),
        "nonce": payload.nonce,
        "input": payload.input,
        "input_selector": payload.input[:10],
        "contract_call": decode_contract_call(payload.input),
    }
    return {
        **await run_in_threadpool(screen_transaction, request.app.state, transaction),
        "contract_call": transaction["contract_call"],
    }


@app.get("/api/model/metrics")
async def model_metrics():
    path = MODEL_PATH.with_name("metrics.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail=f"Metrics are unavailable: {exc}") from exc


@app.get('/api/intelligence/overview')
async def intelligence_overview(request: Request, source: str = Query('simulated', pattern='^(simulated|live|pending|api)$')):
    return await run_in_threadpool(intelligence.overview, request.app.state.store, source)


@app.get('/api/intelligence/wallet/{address}')
async def intelligence_wallet(address: str, request: Request):
    import re
    if not re.fullmatch(r'0x[a-fA-F0-9]{40}', address):
        raise HTTPException(422, 'A full Ethereum wallet address is required')
    return await run_in_threadpool(intelligence.investigation, request.app.state, address.lower())


@app.get('/api/intelligence/wallet/{address}/history')
async def intelligence_history(address: str, request: Request, page: int = Query(1, ge=1),
                               page_size: int = Query(50, ge=1, le=100), until: float | None = Query(None, ge=0)):
    import math
    import re
    if not re.fullmatch(r'0x[a-fA-F0-9]{40}', address) or (until is not None and not math.isfinite(until)):
        raise HTTPException(422, 'A valid wallet address and finite timestamp are required')
    return await run_in_threadpool(intelligence.history_page, request.app.state.store, address.lower(), page, page_size, until)


@app.get('/api/intelligence/alerts')
async def intelligence_alerts(request: Request):
    return await run_in_threadpool(intelligence.alert_list, request.app.state.store)


@app.post('/api/intelligence/alerts/{identifier}/acknowledge', status_code=204)
async def acknowledge_alert(identifier: str, request: Request):
    if not await run_in_threadpool(intelligence.acknowledge, request.app.state.store, identifier):
        raise HTTPException(404, 'Alert not found')


@app.post('/api/intelligence/simulate')
async def simulate_risk(payload: SimulationRequest, request: Request):
    if payload.baseline.sender != payload.scenario.sender:
        raise HTTPException(422, 'Use the same sender for baseline and scenario')
    async with request.app.state.live_lock:
        baseline = await screen_proposed_transaction(payload.baseline, request)
        scenario = await screen_proposed_transaction(payload.scenario, request)

    # Tree thresholds can give two different transfers the same wallet-model
    # probability. Preserve that raw output and add a transparent, bounded
    # scenario-only adjustment for the inputs this tool explicitly compares.
    amount_adjustment = 0.12 * math.tanh(math.log(
        (payload.scenario.value_eth + 1) / (payload.baseline.value_eth + 1)
    ))
    recipient_adjustment = 0.05 if payload.scenario.recipient != payload.baseline.recipient else 0.0
    baseline['model_probability'] = baseline['fraud_probability']
    scenario['model_probability'] = scenario['fraud_probability']
    adjusted_probability = max(0.0, min(
        1.0, scenario['model_probability'] + amount_adjustment + recipient_adjustment
    ))
    scenario['fraud_probability'] = round(adjusted_probability, 4)

    if scenario['observed_events'] >= request.app.state.live_min_wallet_events:
        current = request.app.state.store.current_score(payload.scenario.sender)
        projected = update_score(current, adjusted_probability)
        scenario['projected_trust_score'] = projected.score
        scenario['risk_action'] = projected.action

    return dict(baseline=baseline, scenario=scenario,
                probability_delta=round(scenario['fraud_probability'] - baseline['fraud_probability'], 4),
                trust_delta=round(scenario['projected_trust_score'] - baseline['projected_trust_score'], 2),
                adjustments=dict(
                    amount=round(amount_adjustment, 4),
                    recipient=round(recipient_adjustment, 4),
                    model=round(scenario['model_probability'] - baseline['model_probability'], 4),
                ),
                scope='Read-only estimate: wallet-model output plus a bounded amount adjustment (up to ±12 points) and a 5-point recipient-change adjustment. No score or transaction is saved.')


@app.get("/api/operations/metrics")
async def operational_metrics(request: Request):
    metrics = await run_in_threadpool(request.app.state.store.operational_metrics)
    status = await run_in_threadpool(provider_status)
    last = await run_in_threadpool(request.app.state.store.last_block)
    cache = request.app.state.live_cache or {}
    transactions = cache.get("transactions", [])
    return {
        **metrics,
        "ingestion_leader": request.app.state.live_leader,
        "ingestion_error": request.app.state.live_error,
        "provider_connected": status.get("connected", False),
        "provider_count": status.get("provider_count", 0),
        "block_lag": (
            status["latest_block"] - last["number"]
            if status.get("connected") and last else None
        ),
        "drift_warnings_in_latest_feed": sum(
            bool(item.get("drift_warning")) for item in transactions
        ),
    }


@app.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics(request: Request):
    metrics = await run_in_threadpool(request.app.state.store.operational_metrics)
    numeric = {
        "dwts_wallets_total": metrics["wallets"],
        "dwts_scores_total": metrics["scores"],
        "dwts_chain_blocks_total": metrics["chain_blocks"],
        "dwts_chain_events_total": metrics["chain_events"],
        "dwts_pending_transactions_total": metrics["pending_transactions"],
        "dwts_reviewed_labels_total": metrics["reviewed_labels"],
        "dwts_average_fraud_probability": metrics["average_fraud_probability"],
        "dwts_ingestion_leader": int(request.app.state.live_leader),
        "dwts_ingestion_error": int(bool(request.app.state.live_error)),
        "dwts_pending_ingestion_error": int(bool(request.app.state.pending_error)),
    }
    return "\n".join(f"# TYPE {name} gauge\n{name} {value}" for name, value in numeric.items()) + "\n"


@app.get("/api/operations/audit")
async def recent_audit(request: Request, limit: int = Query(100, ge=1, le=1000)):
    try:
        return await run_in_threadpool(request.app.state.store.audits, limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/labels", status_code=201)
async def create_reviewed_label(payload: ReviewedLabelRequest, request: Request):
    try:
        value = payload.model_dump()
        if payload.address in request.app.state.live_features.wallets:
            value["features"] = request.app.state.live_features.features(payload.address)
        return await run_in_threadpool(request.app.state.store.add_label, value)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/labels")
async def list_reviewed_labels(request: Request, limit: int = Query(100, ge=1, le=1000)):
    try:
        return await run_in_threadpool(request.app.state.store.labels, limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/wallets")
async def search_wallets(
    request: Request,
    query: str = Query("", max_length=42),
    limit: int = Query(50, ge=1, le=200),
):
    try:
        rows = await run_in_threadpool(request.app.state.store.wallets, query, limit)
        return [{**row, "risk_action": risk_action(float(row["score"]))} for row in rows]
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/predict", response_model=PredictionResponse)
async def predict(payload: PredictionRequest, request: Request):
    missing = set(request.app.state.features) - payload.features.keys()
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing {len(missing)} model features")
    probability, score, action, explanation, latency, anomaly = await run_in_threadpool(
        infer_and_update, request, payload.address, payload.features
    )
    return PredictionResponse(
        address=payload.address, fraud_probability=probability, trust_score=score,
        risk_action=action, source="api", explanation=explanation,
        model_version=request.app.state.model_version, latency_ms=latency,
        anomaly_score=anomaly,
    )


@app.get("/api/stream/next", response_model=PredictionResponse)
async def stream_next(request: Request, delay_ms: int = Query(0, ge=0, le=5000)):
    try:
        sequence, row = await request.app.state.stream.next(delay_ms)
        address = str(row["Address"]).lower()
        label = int(row["FLAG"])
        probability, score, action, explanation, latency, anomaly = await run_in_threadpool(
            infer_and_update, request, address, row, 'simulated'
        )
        preview = {name: row.get(name) for name in request.app.state.features[:5]}
        return PredictionResponse(
            address=address, fraud_probability=probability, trust_score=score,
            risk_action=action, sequence=sequence, actual_label=label,
            source="Kaggle historical wallet snapshot", features=preview,
            explanation=explanation, model_version=request.app.state.model_version,
            latency_ms=latency,
            anomaly_score=anomaly,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Stream row is invalid: {exc}") from exc


@app.post("/api/stream/reset", status_code=204)
async def stream_reset(request: Request):
    await request.app.state.stream.reset()


@app.get("/api/wallets/{address}")
async def wallet(address: str, request: Request):
    try:
        result = await run_in_threadpool(request.app.state.store.wallet, address.lower())
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Wallet has no trust-score history")
    return result
