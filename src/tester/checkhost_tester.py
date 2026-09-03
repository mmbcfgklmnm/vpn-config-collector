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

سهمیه‌ی API — درسِ اجرای ۲۰:۱۱ UTC
──────────────────────────────────
با همزمانی ۱۲ و ۶۰۰ endpoint، ۵۸۱ درخواست «HTTP 429» خورد و فقط ۱۳ حکم واقعی
گرفتیم. آن اجرا ۲۲۴۳ کانفیگ را به ۶ رساند و خروجی نهایی صفر شد. دو غلط ساختاری
که آن اجرا نشان داد و اینجا اصلاح شده‌اند:

  • **نرخ**: درخواست‌ها از یک دروازه‌ی مشترک (_Gate) عبور می‌کنند که با هر 429
    فاصله را دو برابر می‌کند و بعد از موفقیت آرام برمی‌گردد. بعد از
    CHECKHOST_MAX_429 خطا، لایه ادامه نمی‌دهد.
  • **«نامعلوم» با «بسته» یکی نیست**: endpoint ای که API دربارهٔ آن جواب نداد
    حکمی ندارد، پس کانفیگ‌هایش *حذف نمی‌شوند* — بدون برچسب IR رد می‌شوند
    (iran_ms = 0). قبلاً 429 مثل «از ایران بسته است» شمرده می‌شد و یک خرابی
    سمت سایت، کل پول را پاک می‌کرد.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import aiohttp

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import cdn, vless
from src.config import (
    CHECKHOST_API_KEY, CHECKHOST_CONCURRENCY, CHECKHOST_MAX_429,
    CHECKHOST_MAX_ENDPOINTS, CHECKHOST_MAX_GAP_SEC, CHECKHOST_MIN_GAP_SEC,
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


class _RateLimited(RuntimeError):
    """۴۲۹ از check-host — جدا از خطاهای دیگر چون رفتار پاسخ متفاوت است."""


class _Gate:
    """دروازه‌ی نرخ مشترک بین همه‌ی workerها، با تنظیم خودکار.

    سهمیه‌ی API عمومی check-host مستند نیست، پس عدد ثابت حدس است. این دروازه
    از جواب خودِ سایت یاد می‌گیرد: هر 429 فاصله را دو برابر می‌کند (تا سقف)،
    هر موفقیت کمی کمش می‌کند. اینطور اجرای اول کند نمی‌شود و اجرایی که به
    دیوار سهمیه می‌خورد، به‌جای سوزاندن ۶۰۰ درخواست، عقب می‌کشد.
    """

    def __init__(self, gap: float, max_gap: float):
        self.gap = max(0.0, gap)
        self.max_gap = max(self.gap, max_gap)
        self.rate_limited = 0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """نوبت گرفتن؛ فاصله‌ی بین ثبت‌ها را تضمین می‌کند."""
        while True:
            async with self._lock:
                now = time.monotonic()
                if now >= self._next:
                    self._next = now + self.gap
                    return
                sleep_for = self._next - now
            await asyncio.sleep(sleep_for)

    async def penalize(self) -> None:
        async with self._lock:
            self.rate_limited += 1
            self.gap = min(self.max_gap, max(0.5, self.gap * 2))
            # صف را هم عقب می‌بریم تا workerهای در انتظار فوراً 429 نگیرند.
            self._next = max(self._next, time.monotonic() + self.gap)

    async def relax(self) -> None:
        async with self._lock:
            if self.gap > CHECKHOST_MIN_GAP_SEC:
                self.gap = max(CHECKHOST_MIN_GAP_SEC, self.gap * 0.8)


async def _submit(session: aiohttp.ClientSession, endpoint: str) -> Optional[str]:
    """ثبت یک تست TCP → request_id.

    نودها صریح فرستاده می‌شوند (`node=` تکراری، طبق مستند API) و از max_nodes
    استفاده نمی‌شود: انتخاب نود کار ما است نه سایت.

    پاسخ موفق `ok: 1` دارد. اگر ok صفر بود متن error خودِ API بالا برده می‌شود
    تا در آمار (top_reasons) دیده شود و — مهم‌تر — چون با پیشوند api به
    check_endpoint می‌رسد، «نامعلوم» حساب شود نه «بسته».
    """
    query = "&".join(f"node={quote(n)}" for n in CHECKHOST_NODES)
    url = f"{API_BASE}/check-tcp?host={quote(endpoint, safe=':')}&{query}"
    async with session.get(url, headers=_headers()) as resp:
        if resp.status == 429:
            raise _RateLimited("HTTP 429")
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        data = await resp.json(content_type=None)
    if not isinstance(data, dict):
        raise RuntimeError("پاسخ نامعتبر")
    if "ok" in data and not data["ok"]:
        raise RuntimeError(str(data.get("error") or "ok=0"))
    request_id = data.get("request_id")
    if not request_id:
        raise RuntimeError("request_id نداد")
    return str(request_id)


def _read_result(
    payload: object, expected: Optional[Iterable[str]] = None
) -> Tuple[bool, int, float]:
    """پاسخ check-result → (کامل شده, تعداد نود موفق, کمترین تأخیر ms).

    معناها طبق مستند API (بخش TCP connection check):
      [{"time": .., "address": ..}] → موفق
      [{"error": ".."}]             → ناموفق
      null                          → هنوز تمام نشده، باید دوباره پرسید

    `expected` نودهایی است که خودمان خواسته‌ایم. فقط همان‌ها شمرده می‌شوند:
    اگر check-host نود دیگری (مثلاً اروپایی) هم برگرداند، شمردنش یعنی سنجش از
    نقطه‌ی اشتباه — دقیقاً همان مشکلی که این لایه برای حلش ساخته شده.
    """
    if not isinstance(payload, dict) or not payload:
        return False, 0, 0.0
    if expected is not None:
        wanted = set(expected)
        payload = {key: value for key, value in payload.items() if key in wanted}
        if not payload:
            return False, 0, 0.0
    if any(value is None for value in payload.values()):
        return False, 0, 0.0

    times: List[float] = []
    for value in payload.values():
        if not isinstance(value, list) or not value:
            continue
        first = value[0]
        # ping نتیجه را تودرتو می‌دهد ([[null]] هم دیده می‌شود)؛ برای tcp فقط
        # dict معنا دارد، پس هرچیز دیگری ناموفق حساب می‌شود.
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
) -> Tuple[bool, int, float]:
    """انتظار برای نتیجه → (حکم گرفتیم؟, تعداد نود موفق, کمترین تأخیر ms).

    عضو اول مهم است: اگر مهلت تمام شود و هیچ نودی جواب نداده باشد، این «صفر
    نود موفق» نیست، «بی‌حکم» است — و بی‌حکم نباید کانفیگ را حذف کند.
    """
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

        done, nodes_ok, ms = _read_result(payload, CHECKHOST_NODES)
        if done:
            return True, nodes_ok, ms
        # نتیجه‌ی نیمه‌کاره هم ارزش دارد: اگر یک نود موفق شده و بقیه معلق
        # مانده‌اند، مهلت که تمام شود همین را برمی‌گردانیم.
        if isinstance(payload, dict):
            partial = {k: v for k, v in payload.items() if v is not None}
            if partial:
                _, part_ok, part_ms = _read_result(partial, CHECKHOST_NODES)
                if part_ok:
                    last_partial = (part_ok, part_ms)
                    # حکم قطعی شده: به‌اندازه‌ی لازم نود وصل شده. با ۷ نود،
                    # منتظر ماندن برای کندترین‌شان فقط وقت می‌سوزاند. تأخیر
                    # گزارش‌شده کمترینِ همین نودهای جواب‌داده است.
                    if part_ok >= CHECKHOST_MIN_NODES:
                        return True, part_ok, part_ms
        await asyncio.sleep(POLL_INTERVAL)

    # مهلت تمام شد: نتیجه‌ی نیمه‌کاره حکم است، هیچی حکم نیست.
    return last_partial[0] > 0, last_partial[0], last_partial[1]


async def check_endpoint(
    session: aiohttp.ClientSession, endpoint: str, gate: Optional[_Gate] = None
) -> Tuple[int, float, str]:
    """یک endpoint → (تعداد نود ایرانی موفق, کمترین تأخیر ms, دلیل خطا).

    دلیل با پیشوند «api:» یعنی حکمی نگرفتیم (سهمیه، خطای سایت، بی‌جواب ماندن).
    آن حالت با «از ایران بسته است» یکی نیست و در دسته، کانفیگ را حذف نمی‌کند.
    """
    if gate is not None:
        await gate.wait()
    try:
        request_id = await _submit(session, endpoint)
    except _RateLimited as exc:
        if gate is not None:
            await gate.penalize()
        return 0, 0.0, f"api: {exc}"
    except Exception as exc:
        return 0, 0.0, f"api: submit {exc}"
    if gate is not None:
        await gate.relax()
    if not request_id:
        # عملاً غیرقابل‌دسترس (‏_submit خودش خطا می‌دهد) ولی اگر بشود، پیشوند
        # api لازم است تا «نامعلوم» شمرده شود نه «بسته».
        return 0, 0.0, "api: submit request_id نداد"

    judged, nodes_ok, ms = await _poll(session, request_id)
    if nodes_ok:
        return nodes_ok, ms, ""
    if not judged:
        return 0, 0.0, "api: نتیجه در مهلت نیامد"
    return 0, 0.0, "از ایران بسته است"


async def iran_latency(endpoints: List[str]) -> Dict[str, float]:
    """داوری چند endpoint خارج از دستهٔ اصلی → {endpoint: ms}.

    برای تأیید «IP تمیز» لازم است: چند IP کلودفلر را از نودهای ایرانی
    می‌سنجد تا کانفیگ‌های احیاشده روی آدرسی سوار نشوند که خودش بسته است.
    عمداً یک تابع کوچک و مستقل است — دستهٔ اصلی سهمیه و بودجهٔ خودش را دارد
    و قاطی کردنشان باعث می‌شد یک IP بد، بودجهٔ کانفیگ‌ها را بخورد.
    ‏۰ یعنی «بسته یا نامعلوم»؛ فراخوان فقط عددِ بزرگ‌تر از صفر را باور می‌کند.
    """
    if SKIP_CHECKHOST or not CHECKHOST_NODES or not endpoints:
        return {}
    gate = _Gate(CHECKHOST_MIN_GAP_SEC, CHECKHOST_MAX_GAP_SEC)
    out: Dict[str, float] = {}
    connector = aiohttp.TCPConnector(limit=2)
    timeout = aiohttp.ClientTimeout(total=CHECKHOST_TIMEOUT_SEC + 20)
    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout, headers=_headers()
    ) as session:
        for endpoint in endpoints:
            nodes_ok, ms, _ = await check_endpoint(session, endpoint, gate)
            out[endpoint] = ms if nodes_ok else 0.0
    return out


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
        "endpoints_blocked": 0,
        "endpoints_unknown": 0,
        "verified": 0,
        "unverified": 0,
        "nodes": list(CHECKHOST_NODES),
    }

    if SKIP_CHECKHOST or not CHECKHOST_NODES:
        logger.info("⏭️  تست ایران رد شد (SKIP_CHECKHOST)")
        stats["skipped"] = True
        stats["passed"] = len(configs)
        stats["unverified"] = len(configs)
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
    gate = _Gate(CHECKHOST_MIN_GAP_SEC, CHECKHOST_MAX_GAP_SEC)
    alive_ms: Dict[str, float] = {}
    blocked: set = set()
    reasons: Dict[str, int] = {}
    checked = 0
    api_errors = 0
    semaphore = asyncio.Semaphore(CHECKHOST_CONCURRENCY)

    connector = aiohttp.TCPConnector(limit=CHECKHOST_CONCURRENCY)
    timeout = aiohttp.ClientTimeout(total=CHECKHOST_TIMEOUT_SEC + 20)

    def _burnt() -> bool:
        """سهمیه تمام شده؛ ادامه دادن فقط 429 بیشتر می‌گیرد."""
        return CHECKHOST_MAX_429 > 0 and gate.rate_limited >= CHECKHOST_MAX_429

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        async def worker(item: Dict) -> None:
            nonlocal checked, api_errors
            if budget.satisfied or _burnt():
                return
            async with semaphore:
                if budget.satisfied or _burnt():
                    return
                endpoint = str(item["endpoint"])
                nodes_ok, ms, reason = await check_endpoint(session, endpoint, gate)
                checked += 1
                if nodes_ok >= CHECKHOST_MIN_NODES:
                    alive_ms[endpoint] = ms
                    await budget.hit()
                    return
                reasons[reason or "نامعلوم"] = reasons.get(reason or "نامعلوم", 0) + 1
                if reason.startswith("api:"):
                    api_errors += 1      # بی‌حکم؛ endpoint حذف نمی‌شود
                else:
                    blocked.add(endpoint)

        await asyncio.gather(*[worker(it) for it in ordered], return_exceptions=True)

    stats["endpoints_checked"] = checked
    stats["endpoints_alive"] = len(alive_ms)
    stats["endpoints_blocked"] = len(blocked)
    stats["endpoints_unknown"] = len(groups) - len(alive_ms) - len(blocked)
    stats["top_reasons"] = dict(sorted(reasons.items(), key=lambda x: -x[1])[:3])
    stats["api_errors"] = api_errors
    stats["rate_limited"] = gate.rate_limited
    if _burnt():
        stats["stopped_rate_limited"] = True
        logger.warning(
            f"⚠️ سهمیه‌ی check-host تمام شد ({gate.rate_limited} پاسخ 429) — "
            "بقیه‌ی endpoint ها بی‌حکم ماندند و حذف نشدند"
        )

    # ── حکم‌ها ──
    # فقط endpoint ای حذف می‌شود که API واقعاً گفته «وصل نشد». تست‌نشده
    # (سهمیه، سقف، توقف زودهنگام، IPv6) بی‌حکم است، پس کانفیگ‌هایش بدون
    # برچسب IR عبور می‌کنند: ms=0 یعنی «تأیید نشده»، نه «رد شده».
    verified: List[Tuple[str, float]] = []
    unverified: List[Tuple[str, float]] = []
    for endpoint, members in groups.items():
        ms = alive_ms.get(endpoint)
        if ms is not None:
            verified.extend((cfg, ms) for cfg in members)
        elif endpoint in blocked:
            continue
        else:
            unverified.extend((cfg, 0.0) for cfg in members)
    unverified.extend((cfg, 0.0) for cfg in unusable)

    verified.sort(key=lambda item: item[1])
    valid = verified + unverified          # تأییدشده‌های ایران همیشه جلوتر

    stats["verified"] = len(verified)
    stats["unverified"] = len(unverified)
    stats["passed"] = len(valid)
    stats["failed"] = len(configs) - len(valid)

    # ── fail-open: اگر API هیچ حکمی نداد، این لایه عملاً اجرا نشده ──
    if not alive_ms and not blocked:
        if api_errors:
            logger.warning(
                f"⚠️ check-host جواب نداد ({api_errors} خطا از {checked}) — "
                "این لایه کنار گذاشته شد تا خروجی صفر نشود"
            )
            stats["skipped"] = True
            stats["fail_open"] = True

    logger.info(
        f"لایه ۴ (ایران): {len(verified)} تأییدشده + {len(unverified)} بی‌حکم "
        f"از {len(configs)} کانفیگ | {len(alive_ms)} زنده، {len(blocked)} بسته، "
        f"{stats['endpoints_unknown']} بی‌حکم از {len(groups)} endpoint"
        + (f" | {len(unusable)} IPv6" if unusable else "")
    )
    return valid, stats
