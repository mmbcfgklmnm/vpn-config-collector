"""لایه ۳: تست TCP — فیلتر سخت و بدون استثنا.

چه چیزی عوض شد و چرا
────────────────────
کاربر ۲۴۸ کانفیگ از خروجی را دستی تست کرد: فقط ۸۸ تا TCP فعال داشتند.
یعنی این لایه داشت endpoint هایی را رد می‌کرد که نباید، و برعکس. دو علت:

۱. تست *per-config* بود، نه per-endpoint. در پول واقعی ۳۵۱۱ کانفیگ فقط
   ۲۴۶۶ endpoint یکتا داشتند؛ یعنی ~۳۰٪ اتصال‌ها تکراری بود و ظرفیت
   همزمانی را بی‌خود مصرف می‌کرد.
۲. یک اتصال موفق کافی بود. اتصال TCP تک‌شانسی است: SYN می‌تواند به یک
   میدل‌باکس بخورد، یا سرور همان لحظه بالا بوده باشد. با TCP_ATTEMPTS=2
   یک endpoint باید *دو بار با فاصله* جواب دهد.

این لایه fail-open ندارد و با هیچ سوئیچی (از جمله SKIP_XRAY) رد نمی‌شود —
درخواست صریح کاربر: «کانفیگ‌های بدون اتصال TCP فعال کاملاً حذف شوند».
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Tuple

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.config import (
    MAX_CONCURRENT_TCP, TCP_ATTEMPT_GAP_SEC, TCP_ATTEMPTS, TCP_TIMEOUT_SEC,
)
from src.logger import get_logger

logger = get_logger("tcp_tester")


def extract_host_port(config: str) -> Tuple[str, int]:
    info = vless.parse(config)
    if info is None:
        return "", 0
    return info.host, info.port


def endpoint_of(config: str) -> str:
    """کلید گروه‌بندی: "host:port" — خالی یعنی غیرقابل تست."""
    host, port = extract_host_port(config)
    if not host or not port:
        return ""
    return f"{host}:{port}"


async def tcp_connect(host: str, port: int) -> Tuple[bool, float]:
    start = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=TCP_TIMEOUT_SEC,
        )
    except Exception:
        return False, 0.0
    ms = (time.monotonic() - start) * 1000
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True, round(ms, 1)


async def tcp_probe(host: str, port: int) -> Tuple[bool, float]:
    """endpoint باید TCP_ATTEMPTS بار موفق شود؛ تأخیر = کمترین اندازه‌گیری.

    اولین شکست کافی است تا endpoint رد شود — بقیه‌ی تلاش‌ها انجام نمی‌شود
    (هم سریع‌تر، هم سخت‌گیرتر).
    """
    attempts = max(1, TCP_ATTEMPTS)
    best = float("inf")
    for index in range(attempts):
        if index:
            await asyncio.sleep(max(0.0, TCP_ATTEMPT_GAP_SEC))
        ok, ms = await tcp_connect(host, port)
        if not ok:
            return False, 0.0
        if ms and ms < best:
            best = ms
    return True, (0.0 if best == float("inf") else round(best, 1))


async def test_tcp_batch(configs: List[str]) -> Tuple[List[Tuple[str, float]], dict]:
    """فیلتر سخت TCP → (لیست (config, tcp_ms)، آمار).

    خروجی per-config است ولی هر endpoint یکتا فقط یک بار تست می‌شود.
    """
    groups: Dict[str, List[str]] = {}
    unparsable = 0
    for cfg in configs:
        endpoint = endpoint_of(cfg)
        if not endpoint:
            unparsable += 1
            continue
        groups.setdefault(endpoint, []).append(cfg)

    logger.info(
        f"🔌 تست TCP — {len(groups)} endpoint یکتا از {len(configs)} کانفیگ "
        f"| {TCP_ATTEMPTS} تلاش لازم | همزمان: {MAX_CONCURRENT_TCP}"
    )

    alive: Dict[str, float] = {}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TCP)

    async def probe(endpoint: str) -> None:
        host, _, port = endpoint.rpartition(":")
        async with semaphore:
            ok, ms = await tcp_probe(host, int(port))
        if ok:
            alive[endpoint] = ms

    started = time.monotonic()
    await asyncio.gather(
        *[probe(ep) for ep in groups], return_exceptions=True
    )

    valid: List[Tuple[str, float]] = []
    for endpoint, members in groups.items():
        ms = alive.get(endpoint)
        if ms is None:
            continue
        valid.extend((cfg, ms) for cfg in members)
    valid.sort(key=lambda item: item[1])

    latencies = [ms for ms in alive.values() if ms > 0]
    stats = {
        "total": len(configs),
        "connected": len(valid),
        "failed": len(configs) - len(valid),
        "unparsable": unparsable,
        "endpoints_total": len(groups),
        "endpoints_alive": len(alive),
        "attempts_required": max(1, TCP_ATTEMPTS),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "duration_seconds": round(time.monotonic() - started, 1),
    }
    logger.info(
        f"لایه ۳ (TCP): {stats['connected']}/{stats['total']} کانفیگ | "
        f"{len(alive)}/{len(groups)} endpoint زنده | "
        f"avg: {stats['avg_latency_ms']}ms | {stats['duration_seconds']}s"
    )
    return valid, stats
