"""
لایه ۶: تست HTTP واقعی از طریق xray

سه اشکال نسخه‌ی قبلی:
  ۱. پورت SOCKS با random.randint انتخاب می‌شد؛ با ۸ تست همزمان و ۱۰هزار
     پورت، برخورد اتفاق می‌افتاد و کانفیگ سالم به‌خاطر پورت اشغال fail می‌شد
     (یا بدتر، ترافیک از تونل کانفیگ دیگری رد می‌شد).
  ۲. بعد از اجرای xray فقط ۱.۵ ثانیه sleep می‌شد و stderr به DEVNULL می‌رفت،
     پس «xray بالا نیامد» از «سرور جواب نداد» قابل تشخیص نبود.
  ۳. پارامترها percent-decode نمی‌شدند، پس path=%2Fws به‌صورت literal به xray
     می‌رسید و همه‌ی کانفیگ‌های WebSocket این‌جا رد می‌شدند.
"""
import asyncio
import json
import os
import socket
import tempfile
import time
from typing import List, Optional, Set, Tuple

import aiohttp
from aiohttp_socks import ProxyConnector

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.config import XRAY_PATH, MAX_CONCURRENT_XRAY, XRAY_TIMEOUT_SEC
from src.logger import get_logger

logger = get_logger("http_tester")

# چند URL تست — اگه یکی جواب داد کافیه
TEST_URLS = [
    ("http://www.gstatic.com/generate_204", 204),
    ("http://cp.cloudflare.com", 204),
    ("http://connectivitycheck.gstatic.com/generate_204", 204),
]

# سقف انتظار برای بالا آمدن inbound محلی xray
XRAY_START_TIMEOUT = 6.0

_used_ports: Set[int] = set()
_port_lock = asyncio.Lock()


async def reserve_port() -> int:
    """گرفتن یک پورت آزاد و رزرو کردنش تا پایان تست.

    بایند به پورت ۰ اجازه می‌ده کرنل پورت آزاد رو انتخاب کنه؛ ست هم جلوی
    استفاده‌ی دوباره در همین اجرا رو می‌گیره.
    """
    async with _port_lock:
        for _ in range(50):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind(("127.0.0.1", 0))
                except OSError:
                    continue
                port = sock.getsockname()[1]
            if port not in _used_ports:
                _used_ports.add(port)
                return port
    raise RuntimeError("پورت آزاد پیدا نشد")


async def release_port(port: int) -> None:
    async with _port_lock:
        _used_ports.discard(port)


def vless_to_xray(config: str, socks_port: int) -> Optional[dict]:
    """لینک VLESS → کانفیگ xray با یک inbound محلی SOCKS5."""
    info = vless.parse(config)
    if info is None or not info.host or not info.uuid:
        return None

    p = info.params
    net = info.network
    fp = p.get("fp", "chrome")
    path = p.get("path", "/")
    host_header = p.get("host") or info.host

    stream: dict = {"network": net}

    if info.security == "reality":
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": info.sni,
            "fingerprint": fp,
            "publicKey": p.get("pbk", ""),
            "shortId": p.get("sid", ""),
            "spiderX": p.get("spx", ""),
        }
    elif info.security in ("tls", "xtls"):
        stream["security"] = "tls"
        stream["tlsSettings"] = {
            "serverName": info.sni,
            "fingerprint": fp,
            # همان رفتار کلاینت واقعی: فقط اگه لینک صریحاً خواسته باشه.
            "allowInsecure": p.get("allowinsecure", "").lower() in ("1", "true"),
        }
        if p.get("alpn"):
            stream["tlsSettings"]["alpn"] = [
                a for a in p["alpn"].split(",") if a.strip()
            ]

    if net == "ws":
        stream["wsSettings"] = {"path": path, "headers": {"Host": host_header}}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": p.get("servicename", "")}
    elif net in ("h2", "http"):
        stream["httpSettings"] = {"host": [host_header], "path": path}

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "127.0.0.1",
            "port": socks_port,
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": False},
        }],
        "outbounds": [{
            "protocol": "vless",
            "settings": {"vnext": [{
                "address": info.host,
                "port": info.port or 443,
                "users": [{
                    "id": info.uuid,
                    "encryption": "none",
                    "flow": p.get("flow", ""),
                }],
            }]},
            "streamSettings": stream,
        }],
    }


async def wait_socks_ready(process, port: int) -> bool:
    """صبر تا وقتی inbound محلی اتصال بپذیره؛ False یعنی xray بالا نیامد."""
    deadline = time.monotonic() + XRAY_START_TIMEOUT
    while time.monotonic() < deadline:
        if process.returncode is not None:
            return False
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=1.0
            )
        except Exception:
            await asyncio.sleep(0.15)
            continue
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    return False


async def http_test_single(config: str) -> Tuple[bool, float, str]:
    """تست HTTP واقعی از طریق xray (اتصال با ProxyConnector سوکس۵)."""
    xray = os.path.abspath(XRAY_PATH)
    if not os.path.exists(xray):
        return False, 0.0, "xray پیدا نشد"

    port = await reserve_port()
    cfg = vless_to_xray(config, port)
    if not cfg:
        await release_port(port)
        return False, 0.0, "تبدیل ناموفق"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(cfg, fh)
        cfg_path = fh.name

    # لاگ xray در فایل نگه داشته میشه نه PIPE، تا پر شدن بافر باعث
    # بلوکه شدن پروسه نشه.
    log_path = cfg_path + ".log"
    process = None
    log_fh = None
    try:
        log_fh = open(log_path, "wb")
        process = await asyncio.create_subprocess_exec(
            xray, "run", "-c", cfg_path,
            stdout=log_fh,
            stderr=asyncio.subprocess.STDOUT,
        )

        if not await wait_socks_ready(process, port):
            detail = ""
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as rfh:
                    detail = rfh.read().strip().replace("\n", " ")[-80:]
            except OSError:
                pass
            return False, 0.0, f"xray بالا نیامد: {detail}" if detail else "xray بالا نیامد"

        connector = ProxyConnector.from_url(f"socks5://127.0.0.1:{port}")
        timeout = aiohttp.ClientTimeout(total=XRAY_TIMEOUT_SEC)

        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            for url, expected_status in TEST_URLS:
                start = time.monotonic()
                try:
                    async with session.get(url, allow_redirects=False) as resp:
                        ms = (time.monotonic() - start) * 1000
                        if resp.status == expected_status:
                            return True, round(ms, 1), ""
                except Exception:
                    continue

        return False, 0.0, "همه URL ها fail شدن"

    except Exception as exc:
        return False, 0.0, str(exc)[:60]
    finally:
        if process is not None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=3)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass
        for path in (cfg_path, log_path):
            try:
                os.unlink(path)
            except OSError:
                pass
        await release_port(port)


async def http_test_batch(
    configs: List[str],
) -> Tuple[List[Tuple[str, float]], dict]:
    valid: List[Tuple[str, float]] = []
    failed = 0
    latencies: List[float] = []
    reasons: dict = {}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_XRAY)

    async def bounded(cfg: str):
        async with semaphore:
            return cfg, await http_test_single(cfg)

    logger.info(f"🌐 تست HTTP {len(configs)} کانفیگ (همزمان: {MAX_CONCURRENT_XRAY})...")
    results = await asyncio.gather(
        *[bounded(c) for c in configs],
        return_exceptions=True,
    )

    for item in results:
        if isinstance(item, BaseException):
            failed += 1
            reasons[type(item).__name__] = reasons.get(type(item).__name__, 0) + 1
            continue
        cfg, (ok, ms, reason) = item
        if ok:
            valid.append((cfg, ms))
            latencies.append(ms)
        else:
            failed += 1
            reasons[reason] = reasons.get(reason, 0) + 1

    # مرتب از سریع‌ترین
    valid.sort(key=lambda x: x[1])

    stats = {
        "total": len(configs),
        "passed": len(valid),
        "failed": failed,
        "avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "best_ms": round(min(latencies), 1) if latencies else 0,
        "top_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])[:3]),
    }
    logger.info(
        f"لایه ۶ (HTTP): {stats['passed']}/{stats['total']} | "
        f"avg: {stats['avg_ms']}ms | best: {stats['best_ms']}ms"
    )
    return valid, stats
