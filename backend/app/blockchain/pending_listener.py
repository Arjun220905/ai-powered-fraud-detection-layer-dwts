"""Provider-visible Ethereum pending-transaction subscription."""

import json
import os

from websockets.asyncio.client import connect

_active_provider = 0


def pending_provider_urls() -> list[str]:
    configured = os.getenv("WEB3_WS_PROVIDER_URLS", "").strip()
    if configured:
        return [url.strip() for url in configured.split(",") if url.strip()]
    configured = os.getenv("WEB3_WS_PROVIDER_URL", "").strip()
    if configured:
        return [configured]
    http_urls = os.getenv("WEB3_PROVIDER_URLS", "").strip() or os.getenv(
        "WEB3_PROVIDER_URL", ""
    ).strip()
    return [
        f"wss://{url.strip().removeprefix('https://')}"
        for url in http_urls.split(",")
        if url.strip().startswith("https://") and "alchemy.com" in url
    ]


def pending_provider_url() -> str:
    return pending_provider_urls()[0] if pending_provider_urls() else ""


def pending_provider_configured() -> bool:
    return bool(pending_provider_url())


async def pending_transactions():
    global _active_provider
    urls = pending_provider_urls()
    if not urls:
        raise RuntimeError("Set WEB3_WS_PROVIDER_URL to enable pending transactions")
    subscription = os.getenv(
        "WEB3_PENDING_SUBSCRIPTION", "alchemy_pendingTransactions"
    )
    params = [subscription]
    if subscription == "alchemy_pendingTransactions":
        params.append({"hashesOnly": False})
    errors = []
    socket = None
    for offset in range(len(urls)):
        index = (_active_provider + offset) % len(urls)
        try:
            socket = await connect(
                urls[index], ping_interval=20, ping_timeout=20,
                close_timeout=2, max_size=4_000_000,
            )
            await socket.send(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": params,
            }))
            acknowledgement = json.loads(await socket.recv())
            if "error" in acknowledgement:
                raise ConnectionError(
                    acknowledgement["error"].get("message", "subscription failed")
                )
            _active_provider = index
            break
        except (ConnectionError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append(f"provider {index + 1}: {type(exc).__name__}")
            if socket is not None:
                await socket.close()
                socket = None
    if socket is None:
        raise ConnectionError(
            f"All {len(urls)} pending providers failed ({', '.join(errors)})"
    )
    try:
        async for message in socket:
            payload = json.loads(message)
            result = payload.get("params", {}).get("result")
            if result:
                yield result
    finally:
        await socket.close()
        await socket.wait_closed()
