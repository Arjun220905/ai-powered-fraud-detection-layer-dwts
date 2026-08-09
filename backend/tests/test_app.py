import os
import sqlite3
from tempfile import TemporaryDirectory

import pandas as pd
from fastapi.testclient import TestClient

from app.blockchain.live_features import LiveWalletFeatures
from app.blockchain.pending_listener import pending_provider_url, pending_provider_urls
from app.blockchain.web3_listener import decode_contract_call, provider_status
from app.ml.train_model import grouped_split_indices
from app.trust_score.db import TrustStore
from app.trust_score.scorer import convergence_steps, update_score


def test_model_split_keeps_wallets_in_one_partition():
    labels = pd.Series([0, 0, 0, 0, 1, 1, 1, 1])
    wallets = pd.Series(["a", "a", "b", "c", "d", "d", "e", "f"])
    train, test = grouped_split_indices(labels, wallets, n_splits=2)
    assert not set(wallets.iloc[train]) & set(wallets.iloc[test])


def test_score_drops_for_high_fraud_probability():
    result = update_score(70, 0.9)
    assert result.score == 55.0
    assert result.action == "Delay"
    assert convergence_steps(70, 1) <= 10
    assert convergence_steps(70, 0) <= 10


def test_blockchain_status_without_provider():
    previous = os.environ.pop("WEB3_PROVIDER_URL", None)
    previous_urls = os.environ.pop("WEB3_PROVIDER_URLS", None)
    try:
        assert provider_status() == {"configured": False, "connected": False}
    finally:
        if previous is not None:
            os.environ["WEB3_PROVIDER_URL"] = previous
        if previous_urls is not None:
            os.environ["WEB3_PROVIDER_URLS"] = previous_urls


def test_alchemy_pending_url_is_derived_when_ws_override_is_blank():
    previous_http = os.environ.get("WEB3_PROVIDER_URL")
    previous_ws = os.environ.get("WEB3_WS_PROVIDER_URL")
    try:
        os.environ["WEB3_PROVIDER_URL"] = "https://eth-mainnet.g.alchemy.com/v2/example"
        os.environ["WEB3_WS_PROVIDER_URL"] = ""
        assert pending_provider_url() == "wss://eth-mainnet.g.alchemy.com/v2/example"
    finally:
        if previous_http is None:
            os.environ.pop("WEB3_PROVIDER_URL", None)
        else:
            os.environ["WEB3_PROVIDER_URL"] = previous_http
        if previous_ws is None:
            os.environ.pop("WEB3_WS_PROVIDER_URL", None)
        else:
            os.environ["WEB3_WS_PROVIDER_URL"] = previous_ws


def test_pending_failover_urls_and_common_contract_call_decode():
    previous = os.environ.get("WEB3_WS_PROVIDER_URLS")
    try:
        os.environ["WEB3_WS_PROVIDER_URLS"] = "wss://one.example, wss://two.example"
        assert pending_provider_urls() == ["wss://one.example", "wss://two.example"]
        target = "1" * 40
        call = decode_contract_call(
            "0xa9059cbb" + ("0" * 24) + target + ("0" * 63) + "a"
        )
        assert call == {
            "selector": "0xa9059cbb", "method": "ERC20 transfer",
            "target": f"0x{target}", "raw_amount": "10",
        }
    finally:
        if previous is None:
            os.environ.pop("WEB3_WS_PROVIDER_URLS", None)
        else:
            os.environ["WEB3_WS_PROVIDER_URLS"] = previous


def test_live_wallet_features_incrementally_update():
    features = LiveWalletFeatures()
    features.observe({
        "from": "0xA", "to": "0xB", "value_eth": "2",
        "gas": None, "gas_used": None, "gas_price_wei": None,
    }, 1_000)
    features.observe({"from": "0xA", "to": "0xC", "value_eth": "3"}, 1_060)
    features.observe({"from": "0xD", "to": "0xA", "value_eth": "4"}, 1_120)
    features.observe_erc20({
        "from": "0xA", "to": "0xE", "token_address": "0xToken", "value": "5",
    }, 1_180)

    wallet = features.features("0xa")
    assert wallet["Sent tnx"] == 2
    assert wallet["Received Tnx"] == 1
    assert wallet["Unique Sent To Addresses"] == 2
    assert wallet["Avg min between sent tnx"] == 1
    assert wallet["total ether balance"] == -1
    assert wallet["Total ERC20 tnxs"] == 1
    assert wallet["ERC20 total ether sent"] == 5
    assert wallet["ERC20 total Ether sent contract"] == 5
    assert wallet["ERC20 uniq sent token name"] == 1
    assert features.advanced_features("0xa")["average_gas_limit"] == 0


def test_chain_state_is_persistent_idempotent_and_reversible():
    with TemporaryDirectory() as directory:
        path = os.path.join(directory, "state.db")
        legacy = sqlite3.connect(path)
        try:
            legacy.executescript("""
                CREATE TABLE score_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL, score REAL NOT NULL,
                    fraud_probability REAL NOT NULL, action TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE chain_blocks (
                    number INTEGER PRIMARY KEY, hash TEXT NOT NULL UNIQUE,
                    parent_hash TEXT NOT NULL, timestamp INTEGER NOT NULL
                );
            """)
        finally:
            legacy.close()
        store = TrustStore(path)
        block = {"number": 10, "hash": "hash", "parent_hash": "parent", "timestamp": 1000}
        event = {
            "block_number": 10, "transaction_hash": "tx", "event_index": 0,
            "kind": "native", "sender": "0xa", "recipient": "0xb",
            "value": "1", "token_address": None,
        }
        store.save_block(block, [event])
        assert store.last_block()["hash"] == "hash"
        assert store.save("0xa", 58, 0.9, "Delay", "live:hash:0xa", 10)
        assert not store.save("0xa", 46, 0.9, "Verify", "live:hash:0xa", 10)
        assert store.wallets("0xa")[0]["score"] == 58
        history = store.wallet("0xa")["history"][0]
        assert history["source_key"] == "live:hash:0xa"
        assert history["block_number"] == 10
        assert history["applied"] == 1
        assert store.acquire_lease("poller", "one", 30)
        assert not store.acquire_lease("poller", "two", 30)
        assert store.allow_request("ip", 2)
        assert store.allow_request("ip", 2)
        assert not store.allow_request("ip", 2)
        pending = {
            "hash": "0x" + "3" * 64, "sender": "0x" + "a" * 40,
            "recipient": "0x" + "b" * 40, "value_eth": "1", "gas": 21000,
            "gas_price_wei": "1", "nonce": 1, "input_selector": "0x",
            "input": "0x",
            "fraud_probability": 0.1, "projected_score": 75, "action": "Approve",
            "confidence": "experimental", "observed_events": 10,
            "decision_ms": 4, "model_version": "test", "explanation": [],
            "contract_call": {"selector": "0xa9059cbb", "method": "ERC20 transfer"},
            "status": "pending",
        }
        store.save_pending(pending)
        assert store.pending()[0]["status"] == "pending"
        assert store.pending()[0]["contract_call"]["method"] == "ERC20 transfer"
        assert store.pending()[0]["input"] == "0x"
        assert store.reconcile_pending([pending["hash"]], 10) == 1
        assert store.pending()[0]["status"] == "confirmed"
        assert store.prune_pending(86400, 0) == 1
        assert store.pending() == []
        store.audit("GET", "/health", 200, "127.0.0.1", 1.5)
        label = store.add_label({
            "transaction_hash": "0x" + "1" * 64,
            "address": "0x" + "a" * 40,
            "label": 1,
            "reviewer": "reviewer",
            "notes": "confirmed",
        })
        assert label["label"] == 1
        assert store.operational_metrics()["reviewed_labels"] == 1
        store.rollback_from(10)
        assert store.last_block() is None
        assert store.wallet("0xa") is None
        store.close()


def test_health_and_stream():
    previous = os.environ.get("WEB3_PROVIDER_URL")
    previous_urls = os.environ.pop("WEB3_PROVIDER_URLS", None)
    previous_key = os.environ.get("API_KEY")
    previous_viewer = os.environ.get("VIEWER_API_KEY")
    os.environ["WEB3_PROVIDER_URL"] = ""
    os.environ["API_KEY"] = "test-key-123456789"
    os.environ["VIEWER_API_KEY"] = "viewer-key-12345678"
    try:
        with TemporaryDirectory() as directory:
            os.environ["DATABASE_PATH"] = os.path.join(directory, "test.db")
            from app.main import app, bounded_catchup_head

            assert bounded_catchup_head(100, 50_000, 1_000) == 1_099

            with TestClient(app) as client:
                assert client.get("/health").json()["status"] == "ok"
                status = client.get("/api/blockchain/status").json()
                assert status["configured"] is False
                assert status["connected"] is False
                assert status["ingestion"]["running"] is False
                assert client.get("/api/blockchain/latest-block").status_code == 503
                response = client.get("/api/stream/next")
                assert response.status_code == 200
                assert 0 <= response.json()["trust_score"] <= 100
                assert response.json()["explanation"]
                assert response.json()["model_version"]
                assert response.json()["latency_ms"] >= 0
                if response.json()["anomaly_score"] is not None:
                    assert 0 <= response.json()["anomaly_score"] <= 1
                assert client.get("/api/wallets?query=0x").status_code == 200
                assert client.post("/api/stream/reset").status_code == 401
                headers = {"X-API-Key": "test-key-123456789"}
                assert client.post("/api/stream/reset", headers=headers).status_code == 204
                screened = client.post("/api/screen-transaction", headers=headers, json={
                    "sender": "0x" + "b" * 40,
                    "recipient": "0x" + "c" * 40,
                    "value_eth": 1,
                    "input": "0xa9059cbb" + ("0" * 24) + ("d" * 40) + ("0" * 63) + "1",
                })
                assert screened.status_code == 200
                assert screened.json()["applied"] is False
                assert screened.json()["latency_ms"] >= 0
                assert screened.json()["contract_call"]["method"] == "ERC20 transfer"
                viewer = {"X-API-Key": "viewer-key-12345678"}
                assert client.post("/api/screen-transaction", headers=viewer, json={
                    "sender": "0x" + "b" * 40, "value_eth": 0,
                }).status_code == 200
                assert client.get("/api/operations/metrics", headers=viewer).status_code == 403
                label = {
                    "transaction_hash": "0x" + "2" * 64,
                    "address": "0x" + "b" * 40,
                    "label": 0,
                    "reviewer": "panel reviewer",
                }
                assert client.post("/api/labels", json=label, headers=headers).status_code == 201
                assert client.get("/api/labels", headers=headers).status_code == 200
                metrics = client.get("/api/operations/metrics", headers=headers)
                assert metrics.status_code == 200
                assert metrics.json()["database_backend"] == "sqlite"
                assert client.get("/metrics", headers=headers).status_code == 200
                preflight = client.options("/api/operations/metrics", headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "x-api-key",
                })
                assert preflight.status_code == 200
                assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
                pending_feed = client.get("/api/blockchain/pending-stream")
                assert pending_feed.status_code == 200
                assert pending_feed.json()["configured"] is False
    finally:
        if previous is None:
            os.environ.pop("WEB3_PROVIDER_URL", None)
        else:
            os.environ["WEB3_PROVIDER_URL"] = previous
        if previous_urls is not None:
            os.environ["WEB3_PROVIDER_URLS"] = previous_urls
        if previous_key is None:
            os.environ.pop("API_KEY", None)
        else:
            os.environ["API_KEY"] = previous_key
        if previous_viewer is None:
            os.environ.pop("VIEWER_API_KEY", None)
        else:
            os.environ["VIEWER_API_KEY"] = previous_viewer
