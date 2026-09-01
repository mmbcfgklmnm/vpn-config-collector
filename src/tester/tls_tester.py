"""
لایه ۴: تست TLS Handshake واقعی
بدون xray — مستقیم به سرور وصل میشه و TLS negotiate می‌کنه

دو اشکال نسخه‌ی قبلی:
  ۱. برای reality تایمر *بعد از* اتصال شروع می‌شد، پس تأخیر همیشه ۰ بود و
     همه‌ی کانفیگ‌های reality بدون برچسب تأخیر می‌موندند (و در sort آخر).
  ۲. تایم‌اوت و سقف همزمانی داخل فایل hardcode بود و از config خوانده نمی‌شد.
"""
import asyncio
import ssl
import time
from typing import List, Tuple

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.config import TLS_TIMEOUT_SEC, MAX_CONCURRENT_TLS
from src.logger import get_logger

logger = get_logger("tls_tester")


def _ssl_context() -> ssl.SSLContext:
    """کانتکست بدون اعتبارسنجی گواهی.

    این‌جا هدف *سنجش زنده بودن* سروره نه اعتماد به آن؛ اکثر این سرورها
    گواهی self-signed یا SNI جعلی دارند و با verify کامل همه رد می‌شدند.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _close(writer) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


async def tls_handshake(host: str, port: int, sni: str) -> Tuple[bool, float]:
    """TLS handshake واقعی؛ زمان از قبل از اتصال اندازه‌گیری میشه."""
    start = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host, port,
                ssl=_ssl_context(),
                server_hostname=sni or host,
            ),
            timeout=TLS_TIMEOUT_SEC,
        )
    except Exception:
        return False, 0.0
    ms = (time.monotonic() - start) * 1000
    await _close(writer)
    return True, round(ms, 1)


async def tcp_connect(host: str, port: int) -> Tuple[bool, float]:
    """اتصال ساده‌ی TCP برای reality — که TLS معمولی negotiate نمی‌کنه."""
    start = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=TLS_TIMEOUT_SEC,
        )
    except Exception:
        return False, 0.0
    ms = (time.monotonic() - start) * 1000
    await _close(writer)
    return True, round(ms, 1)


async def test_tls_single(config: str) -> Tuple[bool, float, str]:
    """
    تست یک کانفیگ
    - reality → فقط TCP (reality داخل خودش TLS رو انجام می‌ده)
    - tls/xtls → TLS handshake واقعی
    """
    info = vless.parse(config)
    if info is None or not info.host:
        return False, 0.0, "پارامتر ناقص"

    host = info.host
    port = info.port or 443
    security = info.security

    if security == "reality":
        ok, ms = await tcp_connect(host, port)
        if ok:
            return True, ms, ""
        return False, 0.0, f"TCP fail به {host}:{port}"

    if security in ("tls", "xtls"):
        ok, ms = await tls_handshake(host, port, info.sni)
        if ok:
            return True, ms, ""
        return False, 0.0, f"TLS handshake fail: {host}:{port}"

    return False, 0.0, f"security نامعتبر: {security}"


async def test_tls_batch(configs: List[str]) -> Tuple[List[Tuple[str, float]], dict]:
    valid: List[Tuple[str, float]] = []
    failed = 0
    latencies: List[float] = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TLS)

    async def bounded(cfg: str):
        async with semaphore:
            return cfg, await test_tls_single(cfg)

    logger.info(f"🔒 تست TLS {len(configs)} کانفیگ...")
    results = await asyncio.gather(*[bounded(c) for c in configs], return_exceptions=True)

    for item in results:
        if isinstance(item, BaseException):
            failed += 1
            continue
        cfg, (ok, ms, _reason) = item
        if ok:
            valid.append((cfg, ms))
            if ms > 0:
                latencies.append(ms)
        else:
            failed += 1

    stats = {
        "total": len(configs),
        "passed": len(valid),
        "failed": failed,
        "avg_tls_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
    }
    logger.info(f"لایه ۴ (TLS): {stats['passed']}/{stats['total']} | avg: {stats['avg_tls_ms']}ms")
    return valid, stats
