"""لایه ۴: تست دسترسی از داخل ایران با API سایت check-host.net

مسئله‌ای که این لایه حل می‌کند
──────────────────────────────
همه‌ی تست‌های محلی pipeline (TCP، TLS، xray) روی رانر گیت‌هاب در آمریکا اجرا
می‌شوند. اندازه‌گیری واقعی روی ۳۰ endpoint از خروجی تأییدشده‌ی همین پروژه:

    از نودهای آلمان/آمریکا/هلند :  ۳۰ از ۳۰ زنده
    از نودهای ایران (تهران/شیراز):   ۱ از ۳۰ زنده

یعنی pipeline «سالم» را درست تشخیص می‌داد ولی «قابل استفاده برای مخاطب» را نه.
این لایه همان چیزی است که کاربر در تست دستی‌اش دید (۲۴۸ → ۸۸ زنده).

نکته‌ی مهم درباره‌ی تفسیر نتیجه
──────────────────────────────
نودهای ایرانی check-host روی شبکه‌ی دیتاسنتر هستند، نه خط خانگی. پس:
  • زنده بودن از ≥۱ نود ایرانی → سیگنال مثبت قوی.
  • مرده بودن از *همه‌ی* نودها → سیگنال منفی قوی، ولی قطعی نیست.
به همین دلیل CHECKHOST_MIN_NODES قابل تنظیم است و اگر این لایه چیزی باقی
نگذارد، pipeline به حالت قبلی برمی‌گردد (fail-open) تا کانال خالی نماند.

صرفه‌جویی در سهمیه
──────────────────
۱. تست روی endpoint یکتا (host:port) انجام می‌شود نه روی هر کانفیگ؛ در پول
   واقعی ۳۵۱۱ کانفیگ فقط ۲۴۶۶ endpoint یکتا داشت.
۲. ترتیب تست با src.cdn اولویت‌بندی می‌شود (CDN اول).
۳. به محض رسیدن به CHECKHOST_TARGET_ALIVE متوقف می‌شود.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import aiohttp

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import cdn, vless
from src.config import (
    CHECKHOST_API_KEY, CHECKHOST_CONCURRENCY, CHECKHOST_MAX_ENDPOINTS,
    CHECKHOST_MIN_NODES, CHECKHOST_NODES, CHECKHOST_TARGET_ALIVE,
    CHECKHOST_TIMEOUT_SEC, SKIP_CHECKHOST,
)
from src.logger import get_logger

logger = get_logger("checkhost")

API_BASE = "https://check-host.net"

# اولین poll زودتر از این جواب کامل نمی‌دهد؛ اندازه‌گیری شده ~۳ ثانیه.
FIRST_POLL_DELAY = 3.0
POLL_INTERVAL = 1.5


def _headers() -> Dict[str, str]:
    """بدون Accept: application/json سایت HTML برمی‌گرداند.

    API عمومی check-host کلید لازم ندارد؛ اگر CHECKHOST_API_KEY ست شده باشد
    فرستادنش بی‌خطر است و برای پلن‌های دارای کلید کار می‌کند.
    """
    headers = {"Accept": "application/json"}
    if CHECKHOST_API_KEY:
        headers["Authorization"] = f"Bearer {CHECKHOST_API_KEY}"
    return headers


def endpoint_of(config: str) -> str:
    """کلید endpoint یک کانفیگ: "host:port" یا رشته‌ی خالی."""
    info = vless.parse(config)
    if info is None or not info.host or not info.port:
        return ""
    return f"{info.host}:{info.port}"


def _is_ipv6(host: str) -> bool:
    return ":" in host.strip("[]")


class _Budget:
    """شمارنده‌ی مشترک برای توقف زودهنگام."""

    def __init__(self, target_alive: int):
        self.target = target_alive
        self.alive = 0
        self._lock = asyncio.Lock()

    async def hit(self) -> None:
        async with self._lock:
            self.alive += 1

    @property
    def satisfied(self) -> bool:
        return self.target > 0 and self.alive >= self.target


async def _submit(session: aiohttp.ClientSession, endpoint: str) -> Optional[str]:
    """ثبت یک تست TCP → request_id."""
    query = "&".join(f"node={quote(n)}" for n in CHECKHOST_NODES)
    url = f"{API_BASE}/check-tcp?host={quote(endpoint, safe=':')}&{query}"
    async with session.get(url, headers=_headers()) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        data = await resp.json(content_type=None)
    if not isinstance(data, dict):
        raise RuntimeError("پاسخ نامعتبر")
    request_id = data.get("request_id")
    return str(request_id) if request_id else None


def _read_result(payload: object) -> Tuple[bool, int, float]:
    """پاسخ check-result → (کامل شده, تعداد نود موفق, کمترین تأخیر ms).

    معناها طبق مستند API:
      [{"time": .., "address": ..}] → موفق
      [{"error": ".."}]             → ناموفق
      null                          → هنوز تمام نشده، باید دوباره پرسید
    """
    if not isinstance(payload, dict) or not payload:
        return False, 0, 0.0
    if any(value is None for value in payload.values()):
        return False, 0, 0.0

    times: List[float] = []
    for value in payload.values():
        if not isinstance(value, list) or not value:
            continue
        first = value[0]
        if isinstance(first, dict) and "time" in first:
            try:
                times.append(float(first["time"]) * 1000.0)
            except (TypeError, ValueError):
                continue
    if not times:
        return True, 0, 0.0
    return True, len(times), round(min(times), 1)


async def _poll(
    session: aiohttp.ClientSession, request_id: str
) -> Tuple[int, float]:
    """انتظار برای نتیجه → (تعداد نود موفق, کمترین تأخیر ms)."""
    deadline = time.monotonic() + CHECKHOST_TIMEOUT_SEC
    await asyncio.sleep(FIRST_POLL_DELAY)
    last_partial: Tuple[int, float] = (0, 0.0)

    while time.monotonic() < deadline:
        try:
            async with session.get(
                f"{API_BASE}/check-result/{quote(request_id)}", headers=_headers()
            ) as resp:
                payload = await resp.json(content_type=None)
        except Exception:
            await asyncio.sleep(POLL_INTERVAL)
            continue

        done, nodes_ok, ms = _read_result(payload)
        if done:
            return nodes_ok, ms
        # نتیجه‌ی نیمه‌کاره هم ارزش دارد: اگر یک نود موفق شده و بقیه معلق
        # مانده‌اند، مهلت که تمام شود همین را برمی‌گردانیم.
        if isinstance(payload, dict):
            partial = {k: v for k, v in payload.items() if v is not None}
            if partial:
                _, part_ok, part_ms = _read_result(partial)
                if part_ok:
                    last_partial = (part_ok, part_ms)
        await asyncio.sleep(POLL_INTERVAL)

    return last_partial


async def check_endpoint(
    session: aiohttp.ClientSession, endpoint: str
) -> Tuple[int, float, str]:
    """یک endpoint → (تعداد نود ایرانی موفق, کمترین تأخیر ms, دلیل خطا)."""
    try:
        request_id = await _submit(session, endpoint)
    except Exception as exc:
        return 0, 0.0, f"submit: {exc}"
    if not request_id:
        return 0, 0.0, "request_id نداد"

    nodes_ok, ms = await _poll(session, request_id)
    if nodes_ok:
        return nodes_ok, ms, ""
    return 0, 0.0, "از ایران بسته است"


async def check_iran_batch(
    configs: List[str],
) -> Tuple[List[Tuple[str, float]], dict]:
    """فیلتر کانفیگ‌ها بر اساس دسترسی TCP از ایران.

    خروجی: (لیست (config, iran_ms), آمار).
    """
    stats: Dict[str, object] = {
        "total": len(configs),
        "passed": 0,
        "failed": 0,
        "skipped": False,
        "endpoints_total": 0,
        "endpoints_checked": 0,
        "endpoints_alive": 0,
        "nodes": list(CHECKHOST_NODES),
    }

    if SKIP_CHECKHOST or not CHECKHOST_NODES:
        logger.info("⏭️  تست ایران رد شد (SKIP_CHECKHOST)")
        stats["skipped"] = True
        stats["passed"] = len(configs)
        return [(c, 0.0) for c in configs], stats

    # ── گروه‌بندی روی endpoint یکتا ──
    groups: Dict[str, List[str]] = {}
    unusable: List[str] = []
    for cfg in configs:
        endpoint = endpoint_of(cfg)
        if not endpoint or _is_ipv6(endpoint.rsplit(":", 1)[0]):
            # IPv6 در پارامتر host:port مبهم است؛ به تست محلی سپرده می‌شود.
            unusable.append(cfg)
            continue
        groups.setdefault(endpoint, []).append(cfg)

    stats["endpoints_total"] = len(groups)

    # ── اولویت‌بندی: CDN اول ──
    ordered = cdn.sort_by_priority([
        {
            "endpoint": endpoint,
            "host": endpoint.rsplit(":", 1)[0],
            "port": int(endpoint.rsplit(":", 1)[1]),
            "network": (vless.parse(members[0]) or vless.Vless()).network,
            "security": (vless.parse(members[0]) or vless.Vless()).security,
        }
        for endpoint, members in groups.items()
    ])
    if CHECKHOST_MAX_ENDPOINTS > 0:
        ordered = ordered[:CHECKHOST_MAX_ENDPOINTS]

    logger.info(
        f"🇮🇷 تست دسترسی از ایران — {len(ordered)} endpoint از {len(groups)} یکتا "
        f"| نودها: {len(CHECKHOST_NODES)} | همزمان: {CHECKHOST_CONCURRENCY}"
    )

    budget = _Budget(CHECKHOST_TARGET_ALIVE)
    alive_ms: Dict[str, float] = {}
    reasons: Dict[str, int] = {}
    checked = 0
    api_errors = 0
    semaphore = asyncio.Semaphore(CHECKHOST_CONCURRENCY)

    connector = aiohttp.TCPConnector(limit=CHECKHOST_CONCURRENCY)
    timeout = aiohttp.ClientTimeout(total=CHECKHOST_TIMEOUT_SEC + 20)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        async def worker(item: Dict) -> None:
            nonlocal checked, api_errors
            if budget.satisfied:
                return
            async with semaphore:
                if budget.satisfied:
                    return
                endpoint = str(item["endpoint"])
                nodes_ok, ms, reason = await check_endpoint(session, endpoint)
                checked += 1
                if nodes_ok >= CHECKHOST_MIN_NODES:
                    alive_ms[endpoint] = ms
                    await budget.hit()
                else:
                    reasons[reason or "نامعلوم"] = reasons.get(reason or "نامعلوم", 0) + 1
                    if reason.startswith("submit:"):
                        api_errors += 1

        await asyncio.gather(*[worker(it) for it in ordered], return_exceptions=True)

    stats["endpoints_checked"] = checked
    stats["endpoints_alive"] = len(alive_ms)
    stats["top_reasons"] = dict(sorted(reasons.items(), key=lambda x: -x[1])[:3])
    stats["api_errors"] = api_errors

    valid: List[Tuple[str, float]] = []
    for endpoint, members in groups.items():
        ms = alive_ms.get(endpoint)
        if ms is None:
            continue
        for cfg in members:
            valid.append((cfg, ms))

    stats["passed"] = len(valid)
    stats["failed"] = len(configs) - len(valid)

    # ── fail-open: اگر خود API از کار افتاده باشد نباید خروجی صفر شود ──
    if not valid and api_errors and api_errors >= max(1, checked // 2):
        logger.warning(
            f"⚠️ check-host جواب نداد ({api_errors} خطا از {checked}) — "
            "این لایه کنار گذاشته شد تا خروجی صفر نشود"
        )
        stats["skipped"] = True
        stats["fail_open"] = True
        stats["passed"] = len(configs)
        return [(c, 0.0) for c in configs], stats

    valid.sort(key=lambda item: item[1])
    logger.info(
        f"لایه ۴ (ایران): {len(valid)}/{len(configs)} کانفیگ | "
        f"{len(alive_ms)}/{checked} endpoint زنده"
        + (f" | {len(unusable)} IPv6 رد شد" if unusable else "")
    )
    return valid, stats
