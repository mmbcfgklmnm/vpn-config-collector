"""
لایه ۳: تست TCP اتصال به host:port
"""
import asyncio
import time
from typing import List, Tuple

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.config import TCP_TIMEOUT_SEC, MAX_CONCURRENT_TCP
from src.logger import get_logger

logger = get_logger("tcp_tester")


def extract_host_port(config: str) -> Tuple[str, int]:
    info = vless.parse(config)
    if info is None:
        return "", 0
    return info.host, info.port


async def tcp_connect(host: str, port: int) -> Tuple[bool, float]:
    start = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=TCP_TIMEOUT_SEC,
        )
        ms = (time.monotonic() - start) * 1000
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True, round(ms, 1)
    except Exception:
        return False, 0


async def test_tcp_batch(configs: List[str]) -> Tuple[List[str], dict]:
    valid = []
    failed = 0
    latencies = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TCP)

    async def test_one(cfg: str):
        async with semaphore:
            host, port = extract_host_port(cfg)
            if not host or not port:
                return cfg, False, 0
            ok, ms = await tcp_connect(host, port)
            return cfg, ok, ms

    logger.info(f"🔌 تست TCP {len(configs)} کانفیگ...")
    results = await asyncio.gather(*[test_one(c) for c in configs], return_exceptions=True)

    for item in results:
        if isinstance(item, BaseException):
            failed += 1
            continue
        cfg, ok, ms = item
        if ok:
            valid.append(cfg)
            latencies.append(ms)
        else:
            failed += 1

    stats = {
        "total": len(configs),
        "connected": len(valid),
        "failed": failed,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
    }
    logger.info(f"لایه ۳ (TCP): {stats['connected']}/{stats['total']} | avg: {stats['avg_latency_ms']}ms")
    return valid, stats
