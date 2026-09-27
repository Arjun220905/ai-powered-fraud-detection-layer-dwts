from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, MetaData,
    String, Table, Text, UniqueConstraint, and_, create_engine, delete, func,
    event, insert, inspect, or_, select, update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

metadata = MetaData()
wallets = Table(
    "wallets", metadata,
    Column("address", String(42), primary_key=True),
    Column("score", Float, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
)
score_history = Table(
    "score_history", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("address", String(42), nullable=False),
    Column("score", Float, nullable=False),
    Column("fraud_probability", Float, nullable=False),
    Column("action", String(16), nullable=False),
    Column("source_key", String(160)),
    Column("block_number", BigInteger),
    Column("applied", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    Index("score_history_block_number_idx", "block_number"),
)
chain_blocks = Table(
    "chain_blocks", metadata,
    Column("number", BigInteger, primary_key=True),
    Column("hash", String(66), nullable=False, unique=True),
    Column("parent_hash", String(66), nullable=False),
    Column("timestamp", BigInteger, nullable=False),
    Column("processed", Boolean, nullable=False, server_default="false"),
)
chain_events = Table(
    "chain_events", metadata,
    Column("block_number", BigInteger, ForeignKey("chain_blocks.number", ondelete="CASCADE"), nullable=False),
    Column("transaction_hash", String(66), primary_key=True),
    Column("event_index", Integer, primary_key=True),
    Column("kind", String(8), primary_key=True),
    Column("sender", String(42), nullable=False),
    Column("recipient", String(42)),
    Column("value", Text, nullable=False),
    Column("token_address", String(42)),
    Column("gas", BigInteger),
    Column("gas_used", BigInteger),
    Column("gas_price_wei", Text),
    Column("nonce", BigInteger),
    Column("input", Text),
    Column("input_selector", String(10)),
    Column("contract_call", Text),
    Column("status", Integer),
    Index("chain_events_block_number_idx", "block_number"),
)
app_metadata = Table(
    "app_metadata", metadata,
    Column("key", String(80), primary_key=True),
    Column("value", Text, nullable=False),
)
worker_leases = Table(
    "worker_leases", metadata,
    Column("name", String(80), primary_key=True),
    Column("owner", String(80), nullable=False),
    Column("expires_at", Float, nullable=False),
)
rate_limits = Table(
    "rate_limits", metadata,
    Column("key", String(120), primary_key=True),
    Column("bucket", BigInteger, primary_key=True),
    Column("count", Integer, nullable=False),
)
audit_log = Table(
    "audit_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("method", String(12), nullable=False),
    Column("path", String(300), nullable=False),
    Column("status", Integer, nullable=False),
    Column("client_ip", String(64), nullable=False),
    Column("duration_ms", Float, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
)
reviewed_labels = Table(
    "reviewed_labels", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("transaction_hash", String(66)),
    Column("address", String(42), nullable=False),
    Column("label", Integer, nullable=False),
    Column("reviewer", String(120), nullable=False),
    Column("notes", Text),
    Column("features", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("transaction_hash", "address", name="reviewed_label_subject"),
)
pending_transactions = Table(
    "pending_transactions", metadata,
    Column("hash", String(66), primary_key=True),
    Column("sender", String(42), nullable=False),
    Column("recipient", String(42)),
    Column("value_eth", Text, nullable=False),
    Column("gas", BigInteger, nullable=False),
    Column("gas_price_wei", Text),
    Column("nonce", BigInteger),
    Column("input_selector", String(10)),
    Column("input", Text),
    Column("contract_call", Text),
    Column("fraud_probability", Float, nullable=False),
    Column("projected_score", Float, nullable=False),
    Column("action", String(16), nullable=False),
    Column("confidence", String(24), nullable=False),
    Column("observed_events", Integer, nullable=False),
    Column("decision_ms", Float, nullable=False),
    Column("model_version", String(32)),
    Column("explanation", Text),
    Column("status", String(16), nullable=False, server_default="pending"),
    Column("first_seen_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("confirmed_block", BigInteger),
)


class TrustStore:
    def __init__(self, location: str):
        self.url = self._url(location)
        self.backend = self.url.split(":", 1)[0].replace("+psycopg", "")
        options = {"pool_pre_ping": True}
        if self.url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False, "timeout": 10}
        self.engine: Engine = create_engine(self.url, **options)
        if self.url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys = ON")
                cursor.execute("PRAGMA journal_mode = WAL")
                cursor.close()
        self.initialize()

    def close(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _url(location: str) -> str:
        if "://" in location:
            return location
        path = Path(location).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path.as_posix()}"

    @staticmethod
    def _dict(row) -> dict:
        return dict(row._mapping)

    def initialize(self) -> None:
        from app import intelligence  # Register additive evidence tables before create_all.
        try:
            metadata.create_all(self.engine)
            existing = {column["name"] for column in inspect(self.engine).get_columns("score_history")}
            block_existing = {column["name"] for column in inspect(self.engine).get_columns("chain_blocks")}
            event_existing = {column["name"] for column in inspect(self.engine).get_columns("chain_events")}
            label_existing = {column["name"] for column in inspect(self.engine).get_columns("reviewed_labels")}
            pending_existing = {column["name"] for column in inspect(self.engine).get_columns("pending_transactions")}
            with self.engine.begin() as db:
                for name, definition in {
                    "source_key": "VARCHAR(160)",
                    "block_number": "BIGINT",
                    "applied": "BOOLEAN NOT NULL DEFAULT TRUE",
                }.items():
                    if name not in existing:
                        db.exec_driver_sql(f"ALTER TABLE score_history ADD COLUMN {name} {definition}")
                if "processed" not in block_existing:
                    db.exec_driver_sql(
                        "ALTER TABLE chain_blocks ADD COLUMN processed BOOLEAN NOT NULL DEFAULT TRUE"
                    )
                if "features" not in label_existing:
                    db.exec_driver_sql("ALTER TABLE reviewed_labels ADD COLUMN features TEXT")
                if "contract_call" not in pending_existing:
                    db.exec_driver_sql("ALTER TABLE pending_transactions ADD COLUMN contract_call TEXT")
                if "input" not in pending_existing:
                    db.exec_driver_sql("ALTER TABLE pending_transactions ADD COLUMN input TEXT")
                for name, definition in {
                    "gas": "BIGINT",
                    "gas_used": "BIGINT",
                    "gas_price_wei": "TEXT",
                    "nonce": "BIGINT",
                    "input_selector": "VARCHAR(10)",
                    "contract_call": "TEXT",
                    "status": "INTEGER",
                }.items():
                    if name not in event_existing:
                        db.exec_driver_sql(
                            f"ALTER TABLE chain_events ADD COLUMN {name} {definition}"
                        )
            with self.engine.begin() as db:
                db.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS score_history_source_key "
                    "ON score_history(source_key)"
                )
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not initialize trust database: {exc}") from exc

    def current_score(self, address: str) -> float:
        return self.current_scores([address])[address]

    def current_scores(self, addresses: list[str]) -> dict[str, float]:
        if not addresses:
            return {}
        try:
            with self.engine.connect() as db:
                rows = db.execute(
                    select(wallets.c.address, wallets.c.score).where(wallets.c.address.in_(addresses))
                )
                existing = {row.address: float(row.score) for row in rows}
                return {address: existing.get(address, 70.0) for address in addresses}
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not read wallet scores: {exc}") from exc

    def _save_rows(self, predictions: list[dict]) -> int:
        saved = 0
        with self.engine.begin() as db:
            for prediction in predictions:
                source = prediction.get("source_key")
                if source and db.execute(
                    select(score_history.c.id).where(score_history.c.source_key == source)
                ).first():
                    continue
                db.execute(insert(score_history).values(
                    address=prediction["address"],
                    score=prediction["score"],
                    fraud_probability=prediction["probability"],
                    action=prediction["action"],
                    source_key=source,
                    block_number=prediction.get("block_number"),
                    applied=bool(prediction.get("applied", True)),
                ))
                exists = db.execute(
                    select(wallets.c.address).where(wallets.c.address == prediction["address"])
                ).first()
                if exists:
                    db.execute(
                        update(wallets).where(wallets.c.address == prediction["address"]).values(
                            score=prediction["score"], updated_at=func.current_timestamp()
                        )
                    )
                else:
                    db.execute(insert(wallets).values(
                        address=prediction["address"], score=prediction["score"]
                    ))
                saved += 1
        return saved

    def save(
        self, address: str, score: float, probability: float, action: str,
        source_key: str | None = None, block_number: int | None = None,
        applied: bool = True,
    ) -> bool:
        try:
            return bool(self._save_rows([{
                "address": address, "score": score, "probability": probability,
                "action": action, "source_key": source_key,
                "block_number": block_number, "applied": applied,
            }]))
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not save wallet score: {exc}") from exc

    def save_many(self, predictions: list[dict]) -> None:
        try:
            self._save_rows(predictions)
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not save wallet-score batch: {exc}") from exc

    def save_block(self, block: dict, events: list[dict]) -> None:
        try:
            with self.engine.begin() as db:
                db.execute(insert(chain_blocks).values(
                    number=block["number"], hash=block["hash"],
                    parent_hash=block["parent_hash"], timestamp=block["timestamp"],
                ))
                if events:
                    db.execute(insert(chain_events), events)
        except IntegrityError as exc:
            raise RuntimeError(
                f"Block {block['number']} conflicts with persisted chain data: {exc}"
            ) from exc
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not persist block {block['number']}: {exc}") from exc

    def last_block(self) -> dict | None:
        try:
            with self.engine.connect() as db:
                row = db.execute(
                    select(chain_blocks).order_by(chain_blocks.c.number.desc()).limit(1)
                ).first()
                return self._dict(row) if row else None
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not read chain cursor: {exc}") from exc

    def mark_block_processed(self, block_number: int) -> None:
        try:
            with self.engine.begin() as db:
                result = db.execute(
                    update(chain_blocks).where(chain_blocks.c.number == block_number).values(processed=True)
                )
                if result.rowcount != 1:
                    raise RuntimeError(f"Block {block_number} was not found")
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not mark block {block_number} processed: {exc}") from exc

    def events(self) -> list[dict]:
        try:
            with self.engine.connect() as db:
                rows = db.execute(
                    select(chain_events, chain_blocks.c.timestamp)
                    .join(chain_blocks, chain_blocks.c.number == chain_events.c.block_number)
                    .order_by(chain_events.c.block_number, chain_events.c.event_index, chain_events.c.kind)
                )
                return [self._dict(row) for row in rows]
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not read persisted chain events: {exc}") from exc

    def rollback_from(self, block_number: int) -> None:
        from app.trust_score.scorer import update_score
        from app.intelligence import decisions, alerts

        try:
            with self.engine.begin() as db:
                affected = select(decisions.c.id).where(decisions.c.block_number >= block_number)
                db.execute(delete(alerts).where(alerts.c.id.in_(affected)))
                db.execute(delete(decisions).where(decisions.c.block_number >= block_number))
                db.execute(delete(score_history).where(score_history.c.block_number >= block_number))
                db.execute(delete(chain_blocks).where(chain_blocks.c.number >= block_number))
                db.execute(delete(app_metadata).where(app_metadata.c.key == "live_cache"))
                db.execute(delete(wallets))
                scores: dict[str, float] = {}
                rows = db.execute(
                    select(
                        score_history.c.id, score_history.c.address,
                        score_history.c.fraud_probability, score_history.c.applied,
                    ).order_by(score_history.c.id)
                ).all()
                for row in rows:
                    current = scores.get(row.address, 70.0)
                    if row.applied:
                        result = update_score(current, float(row.fraud_probability))
                        current, action = result.score, result.action
                    else:
                        action = "Observe"
                    db.execute(
                        update(score_history).where(score_history.c.id == row.id).values(
                            score=current, action=action
                        )
                    )
                    scores[row.address] = current
                if scores:
                    db.execute(insert(wallets), [
                        {"address": address, "score": score}
                        for address, score in scores.items()
                    ])
        except SQLAlchemyError as exc:
            raise RuntimeError(
                f"Could not roll back chain state from block {block_number}: {exc}"
            ) from exc

    def metadata(self, key: str) -> dict | None:
        try:
            with self.engine.connect() as db:
                value = db.execute(
                    select(app_metadata.c.value).where(app_metadata.c.key == key)
                ).scalar_one_or_none()
                return json.loads(value) if value else None
        except (SQLAlchemyError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read metadata {key}: {exc}") from exc

    def save_metadata(self, key: str, value: dict) -> None:
        try:
            encoded = json.dumps(value)
            with self.engine.begin() as db:
                exists = db.execute(
                    select(app_metadata.c.key).where(app_metadata.c.key == key)
                ).first()
                if exists:
                    db.execute(
                        update(app_metadata).where(app_metadata.c.key == key).values(value=encoded)
                    )
                else:
                    db.execute(insert(app_metadata).values(key=key, value=encoded))
        except (SQLAlchemyError, TypeError) as exc:
            raise RuntimeError(f"Could not save metadata {key}: {exc}") from exc

    def wallet(self, address: str) -> dict | None:
        try:
            with self.engine.connect() as db:
                row = db.execute(select(wallets).where(wallets.c.address == address)).first()
                if not row:
                    return None
                history = db.execute(
                    select(
                        score_history.c.id, score_history.c.score,
                        score_history.c.fraud_probability, score_history.c.action,
                        score_history.c.source_key, score_history.c.block_number,
                        score_history.c.applied, score_history.c.created_at,
                    ).where(score_history.c.address == address).order_by(score_history.c.id)
                )
                return {
                    **self._dict(row),
                    "history": [self._dict(item) for item in history],
                }
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not read wallet history: {exc}") from exc

    def wallets(self, query: str = "", limit: int = 50) -> list[dict]:
        try:
            statement = select(wallets).order_by(wallets.c.updated_at.desc()).limit(limit)
            if query:
                statement = statement.where(wallets.c.address.like(f"%{query.lower()}%"))
            with self.engine.connect() as db:
                return [self._dict(row) for row in db.execute(statement)]
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not search wallets: {exc}") from exc

    def acquire_lease(self, name: str, owner: str, ttl_seconds: int) -> bool:
        now = time.time()
        try:
            with self.engine.begin() as db:
                changed = db.execute(
                    update(worker_leases)
                    .where(and_(
                        worker_leases.c.name == name,
                        or_(worker_leases.c.owner == owner, worker_leases.c.expires_at < now),
                    ))
                    .values(owner=owner, expires_at=now + ttl_seconds)
                )
                if changed.rowcount:
                    return True
            try:
                with self.engine.begin() as db:
                    db.execute(insert(worker_leases).values(
                        name=name, owner=owner, expires_at=now + ttl_seconds
                    ))
                return True
            except IntegrityError:
                return False
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not acquire worker lease: {exc}") from exc

    def allow_request(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        bucket = int(time.time() // window_seconds)
        try:
            with self.engine.begin() as db:
                db.execute(delete(rate_limits).where(rate_limits.c.bucket < bucket - 1))
                changed = db.execute(
                    update(rate_limits).where(and_(
                        rate_limits.c.key == key,
                        rate_limits.c.bucket == bucket,
                        rate_limits.c.count < limit,
                    )).values(count=rate_limits.c.count + 1)
                )
                if changed.rowcount:
                    return True
                if db.execute(select(rate_limits.c.key).where(and_(
                    rate_limits.c.key == key, rate_limits.c.bucket == bucket,
                ))).first():
                    return False
                db.execute(insert(rate_limits).values(key=key, bucket=bucket, count=1))
                return True
        except IntegrityError:
            return False
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not apply shared rate limit: {exc}") from exc

    def audit(self, method: str, path: str, status: int, client_ip: str, duration_ms: float) -> None:
        try:
            with self.engine.begin() as db:
                db.execute(insert(audit_log).values(
                    method=method, path=path[:300], status=status,
                    client_ip=client_ip, duration_ms=duration_ms,
                ))
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not write audit log: {exc}") from exc

    def audits(self, limit: int = 100) -> list[dict]:
        try:
            with self.engine.connect() as db:
                rows = db.execute(
                    select(audit_log).order_by(audit_log.c.id.desc()).limit(limit)
                )
                return [self._dict(row) for row in rows]
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not read audit log: {exc}") from exc

    def add_label(self, value: dict) -> dict:
        try:
            value = {**value, "features": json.dumps(value.get("features")) if value.get("features") else None}
            with self.engine.begin() as db:
                result = db.execute(insert(reviewed_labels).values(**value))
                label_id = result.inserted_primary_key[0]
                row = db.execute(
                    select(reviewed_labels).where(reviewed_labels.c.id == label_id)
                ).first()
                result = self._dict(row)
                result["features"] = json.loads(result["features"]) if result["features"] else None
                return result
        except IntegrityError as exc:
            raise ValueError("This transaction/address already has a reviewed label") from exc
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not save reviewed label: {exc}") from exc

    def labels(self, limit: int = 100) -> list[dict]:
        try:
            with self.engine.connect() as db:
                rows = db.execute(
                    select(reviewed_labels).order_by(reviewed_labels.c.id.desc()).limit(limit)
                )
                result = [self._dict(row) for row in rows]
                for row in result:
                    row["features"] = json.loads(row["features"]) if row["features"] else None
                return result
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not read reviewed labels: {exc}") from exc

    def save_pending(self, value: dict) -> None:
        now = time.time()
        encoded = json.dumps(value.get("explanation") or [])
        contract_call = json.dumps(value.get("contract_call")) if value.get("contract_call") else None
        try:
            with self.engine.begin() as db:
                if value.get("nonce") is not None:
                    db.execute(
                        update(pending_transactions).where(and_(
                            pending_transactions.c.sender == value["sender"],
                            pending_transactions.c.nonce == value["nonce"],
                            pending_transactions.c.hash != value["hash"],
                            pending_transactions.c.status == "pending",
                        )).values(status="replaced", updated_at=now)
                    )
                existing = db.execute(
                    select(pending_transactions.c.hash).where(
                        pending_transactions.c.hash == value["hash"]
                    )
                ).first()
                row = {
                    **value, "explanation": encoded, "contract_call": contract_call,
                    "updated_at": now,
                    "first_seen_at": value.get("first_seen_at", now),
                }
                if existing:
                    row.pop("first_seen_at", None)
                    db.execute(
                        update(pending_transactions)
                        .where(pending_transactions.c.hash == value["hash"])
                        .values(**row)
                    )
                else:
                    db.execute(insert(pending_transactions).values(**row))
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not save pending transaction: {exc}") from exc

    def pending(self, limit: int = 25) -> list[dict]:
        try:
            with self.engine.connect() as db:
                rows = db.execute(
                    select(pending_transactions)
                    .order_by(pending_transactions.c.updated_at.desc()).limit(limit)
                )
                result = [self._dict(row) for row in rows]
                for row in result:
                    row["explanation"] = json.loads(row["explanation"] or "[]")
                    row["contract_call"] = json.loads(row["contract_call"]) if row["contract_call"] else None
                return result
        except (SQLAlchemyError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read pending transactions: {exc}") from exc

    def reconcile_pending(self, hashes: list[str], block_number: int) -> int:
        if not hashes:
            return 0
        try:
            with self.engine.begin() as db:
                result = db.execute(
                    update(pending_transactions)
                    .where(pending_transactions.c.hash.in_(hashes))
                    .values(
                        status="confirmed", confirmed_block=block_number,
                        updated_at=time.time(),
                    )
                )
                return result.rowcount or 0
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not reconcile pending transactions: {exc}") from exc

    def expire_pending(self, ttl_seconds: int) -> int:
        try:
            with self.engine.begin() as db:
                result = db.execute(
                    update(pending_transactions).where(and_(
                        pending_transactions.c.status == "pending",
                        pending_transactions.c.updated_at < time.time() - ttl_seconds,
                    )).values(status="dropped", updated_at=time.time())
                )
                return result.rowcount or 0
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not expire pending transactions: {exc}") from exc

    def prune_pending(self, retention_seconds: int, max_records: int = 10000) -> int:
        try:
            with self.engine.begin() as db:
                removed = db.execute(
                    delete(pending_transactions).where(and_(
                        pending_transactions.c.status != "pending",
                        pending_transactions.c.updated_at < time.time() - retention_seconds,
                    ))
                ).rowcount or 0
                total = db.execute(
                    select(func.count()).select_from(pending_transactions)
                ).scalar_one()
                if total > max_records:
                    oldest_terminal = (
                        select(pending_transactions.c.hash)
                        .where(pending_transactions.c.status != "pending")
                        .order_by(pending_transactions.c.updated_at)
                        .limit(total - max_records)
                    )
                    removed += db.execute(
                        delete(pending_transactions).where(
                            pending_transactions.c.hash.in_(oldest_terminal)
                        )
                    ).rowcount or 0
                return removed
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not prune pending transactions: {exc}") from exc

    def prune_chain(self, keep_blocks: int) -> int:
        try:
            with self.engine.begin() as db:
                latest = db.execute(select(func.max(chain_blocks.c.number))).scalar_one_or_none()
                if latest is None:
                    return 0
                result = db.execute(
                    delete(chain_blocks).where(chain_blocks.c.number < latest - keep_blocks + 1)
                )
                return result.rowcount or 0
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not prune old chain events: {exc}") from exc

    def operational_metrics(self) -> dict:
        try:
            with self.engine.connect() as db:
                return {
                    "database_backend": self.backend,
                    "wallets": db.execute(select(func.count()).select_from(wallets)).scalar_one(),
                    "scores": db.execute(select(func.count()).select_from(score_history)).scalar_one(),
                    "chain_blocks": db.execute(select(func.count()).select_from(chain_blocks)).scalar_one(),
                    "chain_events": db.execute(select(func.count()).select_from(chain_events)).scalar_one(),
                    "reviewed_labels": db.execute(select(func.count()).select_from(reviewed_labels)).scalar_one(),
                    "pending_transactions": db.execute(
                        select(func.count()).select_from(pending_transactions)
                    ).scalar_one(),
                    "pending_statuses": dict(db.execute(
                        select(pending_transactions.c.status, func.count())
                        .group_by(pending_transactions.c.status)
                    ).all()),
                    "average_fraud_probability": round(float(
                        db.execute(select(func.avg(score_history.c.fraud_probability))).scalar_one_or_none() or 0
                    ), 4),
                }
        except SQLAlchemyError as exc:
            raise RuntimeError(f"Could not read operational metrics: {exc}") from exc
