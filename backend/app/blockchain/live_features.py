from __future__ import annotations

import copy
from dataclasses import dataclass, field


@dataclass
class DirectionStats:
    count: int = 0
    total: float = 0
    minimum: float | None = None
    maximum: float = 0
    last_timestamp: int | None = None
    interval_minutes: float = 0
    intervals: int = 0
    peers: set[str] = field(default_factory=set)

    def observe(self, value: float, peer: str | None, timestamp: int) -> None:
        if self.last_timestamp is not None:
            self.interval_minutes += max(0, timestamp - self.last_timestamp) / 60
            self.intervals += 1
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        self.last_timestamp = timestamp
        if peer:
            self.peers.add(peer)

    @property
    def average(self) -> float:
        return self.total / self.count if self.count else 0

    @property
    def average_interval(self) -> float:
        return self.interval_minutes / self.intervals if self.intervals else 0


@dataclass
class WalletStats:
    sent: DirectionStats = field(default_factory=DirectionStats)
    received: DirectionStats = field(default_factory=DirectionStats)
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    contracts_created: int = 0
    contract_total: float = 0
    contract_minimum: float | None = None
    contract_maximum: float = 0
    erc20_sent: DirectionStats = field(default_factory=DirectionStats)
    erc20_received: DirectionStats = field(default_factory=DirectionStats)
    erc20_contracts: DirectionStats = field(default_factory=DirectionStats)
    erc20_sent_tokens: set[str] = field(default_factory=set)
    erc20_received_tokens: set[str] = field(default_factory=set)
    gas_total: int = 0
    gas_used_total: int = 0
    gas_price_total: int = 0
    gas_observations: int = 0
    failed_transactions: int = 0
    nonces: set[int] = field(default_factory=set)
    input_selectors: set[str] = field(default_factory=set)
    activity_timestamps: list[int] = field(default_factory=list)

    def touch(self, timestamp: int) -> None:
        self.first_timestamp = timestamp if self.first_timestamp is None else min(self.first_timestamp, timestamp)
        self.last_timestamp = timestamp if self.last_timestamp is None else max(self.last_timestamp, timestamp)


class LiveWalletFeatures:
    """Incremental native-ETH and ERC-20 features rebuilt from persisted events."""

    def __init__(self):
        self.wallets: dict[str, WalletStats] = {}

    def observe(self, transaction: dict, timestamp: int) -> str:
        sender = transaction["from"].lower()
        recipient = transaction.get("to")
        recipient = recipient.lower() if recipient else None
        value = float(transaction["value_eth"])

        sent = self.wallets.setdefault(sender, WalletStats())
        sent.touch(timestamp)
        sent.sent.observe(value, recipient, timestamp)
        sent.activity_timestamps.append(timestamp)
        sent.gas_total += int(transaction.get("gas") or 0)
        sent.gas_used_total += int(transaction.get("gas_used") or 0)
        sent.gas_price_total += int(transaction.get("gas_price_wei") or 0)
        sent.gas_observations += bool(transaction.get("gas") or transaction.get("gas_price_wei"))
        sent.failed_transactions += transaction.get("status") == 0
        if transaction.get("nonce") is not None:
            sent.nonces.add(int(transaction["nonce"]))
        if transaction.get("input_selector") not in {None, "", "0x"}:
            sent.input_selectors.add(transaction["input_selector"])
        if recipient is None:
            sent.contracts_created += 1
            sent.contract_total += value
            sent.contract_minimum = value if sent.contract_minimum is None else min(sent.contract_minimum, value)
            sent.contract_maximum = max(sent.contract_maximum, value)

        if recipient:
            received = self.wallets.setdefault(recipient, WalletStats())
            received.touch(timestamp)
            received.received.observe(value, sender, timestamp)
        return sender

    def observe_erc20(self, transfer: dict, timestamp: int) -> None:
        sender = transfer["from"].lower()
        recipient = transfer["to"].lower()
        token = transfer["token_address"].lower()
        value = float(transfer["value"])

        sent = self.wallets.setdefault(sender, WalletStats())
        sent.touch(timestamp)
        sent.erc20_sent.observe(value, recipient, timestamp)
        sent.erc20_contracts.observe(value, token, timestamp)
        sent.erc20_sent_tokens.add(token)

        received = self.wallets.setdefault(recipient, WalletStats())
        received.touch(timestamp)
        received.erc20_received.observe(value, sender, timestamp)
        received.erc20_received_tokens.add(token)

    def event_count(self, address: str) -> int:
        wallet = self.wallets[address.lower()]
        return wallet.sent.count + wallet.received.count + wallet.erc20_sent.count + wallet.erc20_received.count

    def features(self, address: str) -> dict[str, float | int]:
        wallet = self.wallets[address.lower()]
        sent, received = wallet.sent, wallet.received
        erc20_sent, erc20_received = wallet.erc20_sent, wallet.erc20_received
        erc20_contracts = wallet.erc20_contracts
        duration = ((wallet.last_timestamp or 0) - (wallet.first_timestamp or 0)) / 60
        return {
            "Avg min between sent tnx": sent.average_interval,
            "Avg min between received tnx": received.average_interval,
            "Time Diff between first and last (Mins)": duration,
            "Sent tnx": sent.count,
            "Received Tnx": received.count,
            "Number of Created Contracts": wallet.contracts_created,
            "Unique Received From Addresses": len(received.peers),
            "Unique Sent To Addresses": len(sent.peers),
            "min value received": received.minimum or 0,
            "max value received": received.maximum,
            "avg val received": received.average,
            "min val sent": sent.minimum or 0,
            "max val sent": sent.maximum,
            "avg val sent": sent.average,
            "min value sent to contract": wallet.contract_minimum or 0,
            "max val sent to contract": wallet.contract_maximum,
            "avg value sent to contract": wallet.contract_total / wallet.contracts_created if wallet.contracts_created else 0,
            "total transactions (including tnx to create contract": sent.count + received.count,
            "total Ether sent": sent.total,
            "total ether received": received.total,
            "total ether sent contracts": wallet.contract_total,
            "total ether balance": received.total - sent.total,
            "Total ERC20 tnxs": erc20_sent.count + erc20_received.count,
            "ERC20 total Ether received": erc20_received.total,
            "ERC20 total ether sent": erc20_sent.total,
            "ERC20 total Ether sent contract": erc20_contracts.total,
            "ERC20 uniq sent addr": len(erc20_sent.peers),
            "ERC20 uniq rec addr": len(erc20_received.peers),
            "ERC20 uniq sent addr.1": len(erc20_sent.peers),
            "ERC20 uniq rec contract addr": len(wallet.erc20_received_tokens),
            "ERC20 avg time between sent tnx": erc20_sent.average_interval,
            "ERC20 avg time between rec tnx": erc20_received.average_interval,
            "ERC20 avg time between rec 2 tnx": erc20_received.average_interval,
            "ERC20 avg time between contract tnx": erc20_contracts.average_interval,
            "ERC20 min val rec": erc20_received.minimum or 0,
            "ERC20 max val rec": erc20_received.maximum,
            "ERC20 avg val rec": erc20_received.average,
            "ERC20 min val sent": erc20_sent.minimum or 0,
            "ERC20 max val sent": erc20_sent.maximum,
            "ERC20 avg val sent": erc20_sent.average,
            "ERC20 min val sent contract": erc20_contracts.minimum or 0,
            "ERC20 max val sent contract": erc20_contracts.maximum,
            "ERC20 avg val sent contract": erc20_contracts.average,
            "ERC20 uniq sent token name": len(wallet.erc20_sent_tokens),
            "ERC20 uniq rec token name": len(wallet.erc20_received_tokens),
        }

    def preview(self, transaction: dict, timestamp: int) -> tuple[dict, int]:
        """Score a proposed transaction without changing confirmed wallet history."""
        sender = transaction["from"].lower()
        temporary = LiveWalletFeatures()
        temporary.wallets[sender] = copy.deepcopy(
            self.wallets.get(sender, WalletStats())
        )
        temporary.observe(transaction, timestamp)
        return temporary.features(sender), temporary.event_count(sender)

    def advanced_features(self, address: str, now: int | None = None) -> dict[str, float | int]:
        wallet = self.wallets[address.lower()]
        now = now or wallet.last_timestamp or 0
        timestamps = wallet.activity_timestamps
        return {
            "transactions_1h": sum(value >= now - 3600 for value in timestamps),
            "transactions_24h": sum(value >= now - 86400 for value in timestamps),
            "transactions_7d": sum(value >= now - 604800 for value in timestamps),
            "counterparty_degree": len(wallet.sent.peers | wallet.received.peers),
            "average_gas_limit": (
                wallet.gas_total / wallet.gas_observations if wallet.gas_observations else 0
            ),
            "average_gas_price_wei": (
                wallet.gas_price_total / wallet.gas_observations
                if wallet.gas_observations else 0
            ),
            "average_gas_used": (
                wallet.gas_used_total / wallet.gas_observations
                if wallet.gas_observations else 0
            ),
            "failed_transaction_ratio": (
                wallet.failed_transactions / wallet.sent.count if wallet.sent.count else 0
            ),
            "unique_nonces": len(wallet.nonces),
            "unique_contract_methods": len(wallet.input_selectors),
        }
