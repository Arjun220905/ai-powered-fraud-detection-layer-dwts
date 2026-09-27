"""Persisted investigation evidence and bounded analytics over observed data."""
import asyncio
import json
import math
import os
import smtplib
import ssl
import time
import uuid
from collections import defaultdict
from email.message import EmailMessage
from urllib.request import Request, urlopen

from sqlalchemy import Column, Float, Integer, String, Table, Text, insert, select, update, literal, cast, union_all, func
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.trust_score.db import metadata, chain_blocks, chain_events

decisions = Table(
    'intelligence_decisions', metadata,
    Column('id', String(180), primary_key=True),
    Column('source', String(20), nullable=False, index=True),
    Column('address', String(42), nullable=False, index=True),
    Column('block_number', Integer, index=True),
    Column('timestamp', Float, nullable=False),
    Column('payload', Text, nullable=False),
)
alerts = Table(
    'intelligence_alerts', metadata,
    Column('id', String(180), primary_key=True),
    Column('timestamp', Float, nullable=False),
    Column('payload', Text, nullable=False),
    Column('acknowledged', Integer, nullable=False, default=0),
    Column('delivery', Text, nullable=False, default='{}'),
    Column('attempts', Integer, nullable=False, default=0),
    Column('completed', Integer, nullable=False, default=0),
)


def record(state, source, address, assessment, *, key=None, block_number=None, timestamp=None, **extra):
    value = dict(assessment, **extra, source=source, address=address,
                 id=key or str(uuid.uuid4()), timestamp=timestamp or time.time(), block_number=block_number)
    # No synthetic recipient/ETH volume for historical wallet snapshots.
    with state.store.engine.begin() as db:
        try:
            with db.begin_nested():
                db.execute(insert(decisions).values(**{k: value[k] for k in ('id', 'source', 'address', 'timestamp', 'block_number')}, payload=json.dumps(value, allow_nan=False)))
        except IntegrityError:
            return
        if value.get('fraud_probability', 0) >= 0.8 or (value.get('anomaly_score') or 0) >= 0.8:
            db.execute(insert(alerts).values(id=value['id'], timestamp=value['timestamp'], payload=json.dumps(value)))


def evidence(store, source=None, address=None, limit=2000):
    query = select(decisions).order_by(decisions.c.timestamp.desc(), decisions.c.id.desc()).limit(limit)
    if source:
        query = query.where(decisions.c.source == source)
    if address:
        query = query.where(decisions.c.address == address)
    with store.engine.connect() as db:
        return [json.loads(row.payload) for row in db.execute(query)]


def transactions(store, address=None, limit=2000):
    query = select(chain_events, chain_blocks.c.timestamp).join(chain_blocks).where(chain_blocks.c.processed.is_(True)).order_by(chain_events.c.block_number.desc(), chain_events.c.event_index.desc()).limit(limit)
    if address:
        query = query.where((chain_events.c.sender == address) | (chain_events.c.recipient == address))
    with store.engine.connect() as db:
        return [dict(row._mapping) for row in db.execute(query)]


def overview(store, source):
    rows = evidence(store, source)
    events = transactions(store) if source == 'live' else []
    buckets = defaultdict(lambda: dict(observations=0, suspicious=0, trust_total=0, transactions=0, eth_volume=0))
    for row in rows:
        bucket = buckets[int(row['timestamp'] // 3600) * 3600]
        bucket['observations'] += 1
        bucket['suspicious'] += row['fraud_probability'] >= .8
        bucket['trust_total'] += row.get('trust_score', row.get('projected_trust_score', 70))
    for event in events:
        if event['kind'] == 'native':
            bucket = buckets[int(event['timestamp'] // 3600) * 3600]
            bucket['transactions'] += 1
            if event['status'] != 0:
                bucket['eth_volume'] += float(event['value'])
    trends = [dict(timestamp=stamp, **value,
                   fraud_rate=value['suspicious'] / value['observations'] if value['observations'] else None,
                   dwts=value['trust_total'] / value['observations'] if value['observations'] else None)
              for stamp, value in sorted(buckets.items())]
    latest = {}
    for row in rows:
        latest.setdefault(row['address'], row)
    edges = defaultdict(lambda: dict(count=0, eth_volume=0))
    for item in events:
        if item['recipient']:
            edge = edges[(item['sender'], item['recipient'])]
            edge['count'] += 1
            if item['kind'] == 'native' and item['status'] != 0:
                edge['eth_volume'] += float(item['value'])
    # ponytail: bounded observed graph; connected components flag candidates, not proven fraud rings.
    # Every selected source can provide wallet observations, while only retained
    # confirmed-chain evidence can provide honest sender → recipient edges. Show
    # observed wallets as searchable nodes without inventing relationships.
    addresses = sorted(set(latest) | {a for pair in edges for a in pair})
    risky = {a for a in addresses if latest.get(a, {}).get('fraud_probability', 0) >= .8}
    adjacency = defaultdict(set)
    for sender, recipient in edges:
        if sender in risky and recipient in risky and sender != recipient:
            adjacency[sender].add(recipient)
            adjacency[recipient].add(sender)
    clusters, visited = [], set()
    for address in sorted(adjacency):
        if address in visited:
            continue
        stack, members = [address], []
        visited.add(address)
        while stack:
            node = stack.pop()
            members.append(node)
            for peer in sorted(adjacency[node] - visited):
                visited.add(peer)
                stack.append(peer)
        if len(members) > 1:
            clusters.append(dict(members=sorted(members), size=len(members)))
    latencies = sorted(row['latency_ms'] for row in rows if row.get('latency_ms') is not None)
    histogram = [dict(range=f'{i * 20}–{(i + 1) * 20}%', count=0) for i in range(5)]
    for row in rows:
        histogram[min(4, int(row['fraud_probability'] * 5))]['count'] += 1
    return dict(source=source, sample_limit=2000, observations=len(rows), chain_events=len(events),
                truncated=len(rows) == 2000 or len(events) == 2000, trends=trends,
                graph=dict(nodes=[dict(address=a, probability=latest.get(a, {}).get('fraud_probability')) for a in addresses],
                           edges=[dict(sender=s, recipient=r, **v) for (s, r), v in edges.items()]),
                clusters=clusters, anomalies=[r for r in rows if r['fraud_probability'] >= .8 or (r.get('anomaly_score') or 0) >= .8][:100],
                monitoring=dict(latency_samples=len(latencies), mean_ms=sum(latencies) / len(latencies) if latencies else None,
                                p95_ms=latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else None,
                                drift_warnings=sum(bool(r.get('drift_warning')) for r in rows), distribution=histogram))


def investigation(state, address):
    rows = evidence(state.store, address=address, limit=500)
    events = transactions(state.store, address, 500)
    outgoing = [e for e in events if e['sender'] == address and e['kind'] == 'native']
    receipts = [e for e in outgoing if e['status'] is not None]
    gas = [e['gas_used'] for e in outgoing if e['gas_used'] is not None]
    peers = sorted({a for e in events for a in (e['sender'], e['recipient']) if a and a != address})
    stamps = [e['timestamp'] for e in events]
    duration = max(stamps) - min(stamps) if stamps else 0
    profile = dict(events=len(events), counterparties=peers, outgoing_transactions=len(outgoing),
                   transactions_per_hour=len(events) * 3600 / duration if duration else None,
                   average_gas_used=sum(gas) / len(gas) if gas else None,
                   failure_rate=sum(e['status'] == 0 for e in receipts) / len(receipts) if receipts else None,
                   receipt_samples=len(receipts), first_seen=min(stamps) if stamps else None,
                   last_seen=max(stamps) if stamps else None)
    timeline = [dict(kind='decision', timestamp=r['timestamp'], data=r) for r in rows]
    timeline += [dict(kind='transaction', timestamp=e['timestamp'], data=e) for e in events]
    timeline.sort(key=lambda item: (item['timestamp'], item['kind']))
    return dict(address=address, profile=profile, timeline=timeline, truncated=len(rows) == 500 or len(events) == 500,
                scope='Behavior summary uses the latest 500 retained chain events. History pages include older retained events and decisions. Replay never changes the trust score.')


def history_page(store, address, page=1, page_size=50, until=None):
    """Page a stable, combined timeline in SQL, without the profile's sample cap."""
    until = time.time() if until is None else until
    event_columns = list(chain_events.c)
    recorded = select(
        decisions.c.timestamp.label('timestamp'), literal('decision').label('event_kind'),
        decisions.c.id.label('sort_key'), decisions.c.payload,
        *[cast(literal(None), column.type).label(column.name) for column in event_columns],
    ).where(decisions.c.address == address, decisions.c.timestamp <= until)
    observed = select(
        cast(chain_blocks.c.timestamp, Float).label('timestamp'), literal('transaction').label('event_kind'),
        (chain_events.c.transaction_hash + ':' + cast(chain_events.c.event_index, String) + ':' + chain_events.c.kind).label('sort_key'),
        cast(literal(None), Text).label('payload'), *event_columns,
    ).join(chain_blocks).where(
        chain_blocks.c.processed.is_(True), chain_blocks.c.timestamp <= until,
        (chain_events.c.sender == address) | (chain_events.c.recipient == address),
    )
    combined = union_all(recorded, observed).subquery()
    with store.engine.connect() as db:
        total = db.scalar(select(func.count()).select_from(combined))
        rows = db.execute(select(combined).order_by(combined.c.timestamp.desc(), combined.c.event_kind, combined.c.sort_key).offset((page - 1) * page_size).limit(page_size))
        items = []
        for row in rows.mappings():
            data = json.loads(row['payload']) if row['event_kind'] == 'decision' else {column.name: row[column.name] for column in event_columns}
            items.append(dict(kind=row['event_kind'], timestamp=row['timestamp'], data=data))
    return dict(items=items, page=page, page_size=page_size, total=total, until=until,
                has_more=page * page_size < total)


def alert_list(store):
    with store.engine.connect() as db:
        return [dict(id=r.id, timestamp=r.timestamp, acknowledged=bool(r.acknowledged),
                     event=json.loads(r.payload), delivery=json.loads(r.delivery), attempts=r.attempts)
                for r in db.execute(select(alerts).order_by(alerts.c.timestamp.desc()).limit(100))]


def acknowledge(store, identifier):
    with store.engine.begin() as db:
        return db.execute(update(alerts).where(alerts.c.id == identifier).values(acknowledged=1)).rowcount


def deliver_alerts(state):
    if not state.store.acquire_lease('intelligence_alert_delivery', state.worker_id, 120):
        return
    with state.store.engine.connect() as db:
        pending = db.execute(select(alerts).where(alerts.c.attempts < 3, alerts.c.completed == 0).order_by(alerts.c.timestamp).limit(5)).all()
    for row in pending:
        delivery = json.loads(row.delivery)
        event = json.loads(row.payload)
        body = json.dumps(dict(id=row.id, service='dwts', kind='high_risk', event=event))
        channels = []
        if os.getenv('ALERT_WEBHOOK_URL'):
            channels.append('webhook')
        if os.getenv('ALERT_SMTP_HOST') and os.getenv('ALERT_EMAIL_TO') and os.getenv('ALERT_EMAIL_FROM'):
            channels.append('email')
        outstanding = [channel for channel in channels if delivery.get(channel) != 'sent']
        if not outstanding:
            continue
        for channel in outstanding:
            try:
                if channel == 'webhook':
                    with urlopen(Request(os.environ['ALERT_WEBHOOK_URL'], data=body.encode(), headers={'Content-Type': 'application/json', 'Idempotency-Key': row.id}), timeout=5) as response:
                        if not 200 <= response.status < 300:
                            raise OSError('Delivery rejected')
                else:
                    message = EmailMessage()
                    message['From'], message['To'] = os.environ['ALERT_EMAIL_FROM'], os.environ['ALERT_EMAIL_TO']
                    message['Subject'] = f"DWTS high-risk observation: {event['address']}"
                    message.set_content(body)
                    with smtplib.SMTP(os.environ['ALERT_SMTP_HOST'], int(os.getenv('ALERT_SMTP_PORT', '587')), timeout=5) as smtp:
                        smtp.starttls(context=ssl.create_default_context())
                        if os.getenv('ALERT_SMTP_USER'):
                            smtp.login(os.environ['ALERT_SMTP_USER'], os.environ.get('ALERT_SMTP_PASSWORD', ''))
                        smtp.send_message(message)
                delivery[channel] = 'sent'
            except (OSError, ValueError, smtplib.SMTPException):
                delivery[channel] = 'failed'
        with state.store.engine.begin() as db:
            db.execute(update(alerts).where(alerts.c.id == row.id).values(delivery=json.dumps(delivery), attempts=row.attempts + 1, completed=int(all(delivery.get(c) == 'sent' for c in channels))))


async def alert_worker(state):
    while True:
        try:
            await run_in_threadpool(deliver_alerts, state)
        except Exception:
            import logging
            logging.getLogger('dwts').exception('Alert worker failed')
        await asyncio.sleep(30)
