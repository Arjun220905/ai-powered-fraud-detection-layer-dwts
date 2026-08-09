"""Ethereum confirmed-block reader; dataset replay remains available for the demo."""

import os
from concurrent.futures import ThreadPoolExecutor

from web3.exceptions import Web3Exception

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_token_decimals: dict[str, tuple[int, str]] = {}
_active_provider = 0

COMMON_CALLS = {
    "0xa9059cbb": ("ERC20 transfer", 1, 2),
    "0x095ea7b3": ("ERC20 approve", 1, 2),
    "0x23b872dd": ("ERC20 transferFrom", 2, 3),
    "0x38ed1739": ("swapExactTokensForTokens", None, None),
    "0x7ff36ab5": ("swapExactETHForTokens", None, None),
}


def decode_contract_call(data: str) -> dict | None:
    """Decode a small, reliable set of common selectors without guessing an ABI."""
    if not isinstance(data, str) or len(data) < 10:
        return None
    selector = data[:10].lower()
    definition = COMMON_CALLS.get(selector)
    if not definition:
        return {"selector": selector, "method": "unknown"} if selector != "0x" else None
    method, address_word, amount_word = definition
    result = {"selector": selector, "method": method}
    words = [data[index:index + 64] for index in range(10, len(data), 64)]
    try:
        if address_word and len(words) >= address_word:
            result["target"] = f"0x{words[address_word - 1][-40:]}"
        if amount_word and len(words) >= amount_word:
            result["raw_amount"] = str(int(words[amount_word - 1], 16))
    except ValueError:
        result["decode_error"] = "invalid ABI words"
    return result


def provider_urls() -> list[str]:
    configured = os.getenv("WEB3_PROVIDER_URLS", "").strip()
    if not configured:
        configured = os.getenv("WEB3_PROVIDER_URL", "").strip()
    return [url.strip() for url in configured.split(",") if url.strip()]


def provider_configured() -> bool:
    return bool(provider_urls())


def _transaction(web3, transaction) -> dict:
    data = transaction.get("input", "0x")
    data = data.hex() if hasattr(data, "hex") else str(data)
    return {
        "hash": transaction["hash"].hex() if hasattr(transaction["hash"], "hex") else transaction["hash"],
        "from": transaction["from"],
        "to": transaction.get("to"),
        "value_eth": str(web3.from_wei(transaction["value"], "ether")),
        "gas": int(transaction["gas"]),
        "gas_price_wei": str(transaction.get("gasPrice") or transaction.get("maxFeePerGas") or 0),
        "nonce": int(transaction["nonce"]),
        "input": data,
        "input_selector": data[:10] if len(data) >= 10 else data,
        "contract_call": decode_contract_call(data),
    }


def _run(operation):
    from web3 import Web3

    global _active_provider
    urls = provider_urls()
    if not urls:
        raise RuntimeError("Set WEB3_PROVIDER_URLS or WEB3_PROVIDER_URL")
    errors = []
    for offset in range(len(urls)):
        index = (_active_provider + offset) % len(urls)
        try:
            web3 = Web3(Web3.HTTPProvider(urls[index], request_kwargs={"timeout": 10}))
            if not web3.is_connected():
                raise ConnectionError("connection check failed")
            result = operation(web3)
            _active_provider = index
            return result
        except (ConnectionError, OSError, ValueError, Web3Exception) as exc:
            errors.append(f"provider {index + 1}: {type(exc).__name__}")
    raise ConnectionError(f"All {len(urls)} Ethereum providers failed ({', '.join(errors)})")


def current_block_number() -> int:
    return int(_run(lambda web3: web3.eth.block_number))


def provider_status() -> dict:
    if not provider_configured():
        return {"configured": False, "connected": False}
    try:
        chain_id, latest = _run(lambda web3: (web3.eth.chain_id, web3.eth.block_number))
        return {
            "configured": True, "connected": True, "chain_id": chain_id,
            "latest_block": latest, "provider_count": len(provider_urls()),
            "active_provider": _active_provider + 1,
        }
    except (ConnectionError, OSError, ValueError) as exc:
        return {"configured": True, "connected": False, "error": str(exc)}


def _decimals(web3, token: str) -> tuple[int, str]:
    if token in _token_decimals:
        return _token_decimals[token]
    try:
        raw = web3.eth.call({"to": web3.to_checksum_address(token), "data": "0x313ce567"})
        value = int.from_bytes(raw, "big")
        if not 0 <= value <= 36:
            raise ValueError(f"invalid decimals value {value}")
        result = (value, "contract")
    except (OSError, ValueError, Web3Exception):
        # Some legacy/non-standard tokens do not implement decimals().
        result = (18, "fallback_18")
    _token_decimals[token] = result
    return result


def _block_transactions(web3, block_identifier: int | str, limit: int | None) -> dict:
    block = web3.eth.get_block(block_identifier, full_transactions=True)
    transactions = block["transactions"] if limit is None else block["transactions"][-limit:]
    logs = web3.eth.get_logs({
        "fromBlock": block["number"],
        "toBlock": block["number"],
        "topics": [TRANSFER_TOPIC],
    })
    tokens = {log["address"].lower() for log in logs if len(log["topics"]) >= 3 and len(log["data"])}
    # ponytail: bounded threads hide independent token metadata RPC latency; use provider batching at higher volume.
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda token: _decimals(web3, token), tokens))
    transfers = []
    for log in logs:
        if len(log["topics"]) < 3 or len(log["data"]) == 0:
            continue
        token = log["address"].lower()
        decimals, decimals_source = _decimals(web3, token)
        transfers.append({
            "transaction_hash": log["transactionHash"].hex(),
            "log_index": int(log["logIndex"]),
            "token_address": token,
            "from": f"0x{log['topics'][1].hex()[-40:]}",
            "to": f"0x{log['topics'][2].hex()[-40:]}",
            "value": str(int.from_bytes(log["data"], "big") / (10 ** decimals)),
            "decimals": decimals,
            "decimals_source": decimals_source,
        })
    normalized = [_transaction(web3, transaction) for transaction in transactions]
    if os.getenv("LIVE_INCLUDE_RECEIPTS", "").lower() in {"1", "true", "yes"}:
        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(
                lambda transaction: web3.eth.get_transaction_receipt(transaction["hash"]),
                transactions,
            ))
        for transaction, receipt in zip(normalized, receipts):
            transaction["status"] = int(receipt["status"])
            transaction["gas_used"] = int(receipt["gasUsed"])
    return {
        "number": block["number"],
        "hash": block["hash"].hex(),
        "parent_hash": block["parentHash"].hex(),
        "timestamp": block["timestamp"],
        "transaction_count": len(block["transactions"]),
        "transactions": normalized,
        "erc20_transfers": transfers,
    }


def block_transactions(block_identifier: int | str = "latest", limit: int | None = 25) -> dict:
    if limit is not None and not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    return _run(lambda web3: _block_transactions(web3, block_identifier, limit))


def latest_block_transactions(limit: int | None = 25) -> dict:
    return block_transactions("latest", limit)


def transaction_by_hash(transaction_hash: str) -> dict | None:
    def fetch(web3):
        transaction = web3.eth.get_transaction(transaction_hash)
        return _transaction(web3, transaction) if transaction else None
    try:
        return _run(fetch)
    except (ConnectionError, OSError, ValueError, Web3Exception):
        return None
