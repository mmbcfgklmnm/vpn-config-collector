"""
لایه ۶: بررسی موقعیت جغرافیایی سرور
سرور نباید در کشورهای بلاک‌شده باشه (پیش‌فرض: IR, KP)

اصلاحات نسبت به نسخه‌ی قبلی:
  ۱. BLOCKED_COUNTRIES از config خوانده میشه (قبلاً RU و CN هم داخلش بود و
     حجم زیادی از سرورهای سالم رو حذف می‌کرد).
  ۲. پیشوندهای غلط «IP ایران» حذف شدند: 192.99 (OVH کانادا)، 193.0 (RIPE NCC
     هلند)، 91.108 (تلگرام) و 185.1/2/3/10 که فضای IXP اروپاست. این‌ها
     سرورهای سالم رو به‌عنوان ایران رد می‌کردند.
  ۳. کد کشور اگر ۲ حرفی نبود دیگر با upper()[:2] بریده نمیشه
     (Netherlands → "NE" که هیچ کشوری نیست)؛ از جدول نام استفاده میشه.

چرا این لایه بازنویسی شد
────────────────────────
شکایت کاربر: «چرا انتخاب کشور فقط آمریکا است؟» علتش این‌جا بود، نه در ربات.
اجرای ۱۷:۴۶ از ۶۶۰۳ کانفیگِ پاس‌کرده، کشور ۴۷۵۴ تا را «نامعلوم» گذاشت، پس
دکمه‌ی کشور در ربات فقط یک گزینه داشت. سه علت داشت و هر سه اصلاح شد:

  • **کوئری به‌ازای هر کانفیگ بود، نه هر IP.** ادعای نسخه‌ی قبلی («IP ها
    یکتا query میشن») درست نبود: هر کانفیگ خودش resolve و query می‌کرد و
    ۶۶۰۰ کوئریِ همزمان از کنار cache رد می‌شدند. حالا مسیر سه‌مرحله‌ای است:
    میزبان‌های یکتا → resolve، IP های یکتا → یک کوئری، بعد حکمِ هر کانفیگ
    از روی نقشه‌ها بدون شبکه. نسبت واقعی داده: ۲۴۰۰ کانفیگ ≈ ۱۰۰۰ میزبان
    یکتا، و چون endpoint های CDN روی IP مشترک می‌نشینند، IP یکتا از آن هم
    خیلی کمتر است.
  • **`https://ip-api.com` روی پلن رایگان کار نمی‌کند.** با درخواست واقعی
    آزمایش شد: ‏403 با بدنه‌ی خالی. یعنی سومین API عملاً هیچ‌وقت جواب نداده
    و بی‌صدا رد می‌شده. حالا مسیر اصلی `http://ip-api.com/batch` است
    (۱۰۰ IP در یک POST، سقف ۱۵ POST در دقیقه، فقط HTTP روی پلن رایگان).
  • **یک ۴۲۹ کل اجرا را کور می‌کرد.** `_disabled_apis` برای همیشه API را
    کنار می‌گذاشت. حالا اول به X-Ttl احترام گذاشته و صبر می‌شود؛ کنار
    گذاشتن فقط پس از تکرارِ ۴۲۹ اتفاق می‌افتد.

نکته‌ی امنیتی: batch روی پلن رایگان TLS ندارد و آدرس‌های سرور را رمزنشده
می‌فرستد. این‌ها همان IP هایی هستند که در لینک اشتراک عمومی منتشر می‌شوند،
پس چیزی که خودش عمومی است لو نمی‌رود — ولی هیچ داده‌ی کاربر (توکن، شناسه‌ی
تلگرام، کانفیگ اهدایی) از این مسیر عبور نمی‌کند و نباید بکند.
"""
import asyncio
import ipaddress
import json
import socket
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple

import aiohttp

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.config import (
    BLOCKED_COUNTRIES, GEO_BATCH_SIZE, GEO_BUDGET_SEC, MAX_CONCURRENT_GEO,
)
from src.logger import get_logger

logger = get_logger("geo_checker")

# پیشوندهای IP ایران — فقط برای رد سریع بدون API.
# هرچیزی که این‌جا نباشه به API سپرده میشه، پس ناقص بودن لیست خطرناک نیست؛
# اشتباه بودنش خطرناکه.
IRAN_IP_PREFIXES = (
    "2.144.", "2.145.", "2.176.", "2.177.", "2.178.",
    "5.22.", "5.23.", "5.52.", "5.53.", "5.56.", "5.57.", "5.61.",
    "31.2.", "31.7.", "31.14.", "31.24.", "31.25.",
    "46.100.", "46.102.", "46.143.", "46.209.",
    "78.38.", "78.39.", "78.157.", "78.158.", "78.159.",
    "80.66.", "80.191.", "80.210.",
    "82.99.", "82.100.", "82.138.",
    "85.9.", "85.15.", "85.131.", "85.132.", "85.133.", "85.134.",
    "91.92.", "91.98.", "91.186.", "91.207.", "91.212.",
    "94.74.", "94.182.", "94.184.",
    "188.208.", "188.209.", "188.210.", "188.211.",
    "193.140.", "193.141.",
    "194.225.", "194.226.",
    "195.146.", "195.147.",
)

# مسیر اصلی: یک POST برای ۱۰۰ IP. روی پلن رایگان فقط HTTP (نسخه‌ی https
# با ۴۰۳ و بدنه‌ی خالی جواب می‌دهد — با درخواست واقعی آزمایش شد).
GEO_BATCH_API = "http://ip-api.com/batch?fields=status,countryCode,query"

# مسیر پشتیبان، تک‌به‌تک — برای IP هایی که batch جوابشان را نداد.
GEO_APIS = [
    "https://api.country.is/{ip}",
    "https://ipinfo.io/{ip}/json",
    "http://ip-api.com/json/{ip}?fields=countryCode",
]

# چند ۴۲۹ پشت‌سرهم تا یک API کنار گذاشته شود. یکی کافی نیست: سهمیه‌ی
# ip-api پنجره‌ای ۶۰ ثانیه‌ای است و بعد از انتظار دوباره باز می‌شود، ولی
# نسخه‌ی قبلی با همان یک ۴۲۹ برای همیشه کنارش می‌گذاشت و بقیه‌ی اجرا کور
# می‌شد — همان چیزی که ۴۷۵۴ کانفیگ را بی‌کشور گذاشت.
GEO_MAX_429 = 2
# سقف انتظار برای هر X-Ttl. سرور معمولاً ۶۰ می‌دهد؛ سقف جلوی عدد پرت را
# می‌گیرد تا لایه ۶ روی یک هدر عجیب گیر نکند.
GEO_WAIT_CAP_SEC = 65

# نام کامل کشورهایی که ممکنه به‌جای کد دوحرفی برگردن.
COUNTRY_NAMES = {
    "IRAN": "IR",
    "IRAN, ISLAMIC REPUBLIC OF": "IR",
    "NORTH KOREA": "KP",
    "KOREA, DEMOCRATIC PEOPLE'S REPUBLIC OF": "KP",
}

UNKNOWN = "??"

_geo_cache: Dict[str, str] = {}
_disabled_apis: Set[str] = set()
_api_429: Dict[str, int] = {}


def is_iran_ip_simple(ip: str) -> bool:
    """رد سریع IP ایران بدون API."""
    return ip.startswith(IRAN_IP_PREFIXES)


def normalize_country(raw: object) -> str:
    """کد دوحرفی معتبر یا رشته‌ی خالی."""
    text = str(raw or "").strip()
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return COUNTRY_NAMES.get(text.upper(), "")


async def resolve_host(host: str) -> Optional[str]:
    """hostname → IP. اگه خودش IP باشه همون برمی‌گرده (IPv6 هم پشتیبانی میشه)."""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP),
        )
    except Exception:
        return None
    for info in infos:
        addr = info[4][0]
        if addr:
            return addr
    return None


async def get_country(ip: str, session: aiohttp.ClientSession) -> Optional[str]:
    """کد کشور یک IP از API های تک‌به‌تک. None یعنی هیچ‌کدام جواب ندادند."""
    cached = _geo_cache.get(ip)
    if cached:
        return cached

    for api_url in GEO_APIS:
        if api_url in _disabled_apis:
            continue
        try:
            async with session.get(
                api_url.format(ip=ip),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 429:
                    # صبر می‌کنیم، نه اینکه API را دور بیندازیم: پنجره‌ی
                    # سهمیه چند ثانیه‌ای است و کنار گذاشتنِ زودهنگام یعنی
                    # بقیه‌ی اجرا بی‌کشور بماند.
                    if _note_429(api_url, resp.headers.get("X-Ttl")):
                        await asyncio.sleep(_wait_for(resp.headers.get("X-Ttl")))
                    continue
                if resp.status != 200:
                    continue
                data = await resp.json(content_type=None)
        except Exception:
            continue

        if not isinstance(data, dict):
            continue
        country = normalize_country(
            data.get("countryCode") or data.get("country") or data.get("Country")
        )
        if country:
            _geo_cache[ip] = country
            return country
    return None


def _wait_for(ttl_header: object) -> float:
    """X-Ttl → ثانیه‌ی انتظار، با سقف."""
    try:
        return max(1.0, min(float(str(ttl_header)), GEO_WAIT_CAP_SEC))
    except (TypeError, ValueError):
        return 5.0


def _note_429(api_url: str, ttl_header: object) -> bool:
    """ثبت یک ۴۲۹ → آیا ارزش صبر کردن دارد؟

    False یعنی این API به سقف GEO_MAX_429 رسید و کنار گذاشته شد؛ صبر کردن
    برایش فقط وقت لایه ۶ را می‌خورد.
    """
    hits = _api_429.get(api_url, 0) + 1
    _api_429[api_url] = hits
    if hits >= GEO_MAX_429:
        _disabled_apis.add(api_url)
        logger.warning(
            f"⚠️ {hits} بار rate-limit روی {api_url} — کنار گذاشته شد"
        )
        return False
    logger.info(f"   rate-limit روی {api_url} — {_wait_for(ttl_header):.0f}s صبر")
    return True


def _chunks(items: List[str], size: int) -> Iterable[List[str]]:
    size = max(1, size)
    for start in range(0, len(items), size):
        yield items[start:start + size]


async def _post_batch(
    chunk: List[str], session: aiohttp.ClientSession
) -> Optional[Tuple[object, object, object]]:
    """یک POST با تلاش دوباره پس از ۴۲۹ → (جواب, X-Rl, X-Ttl) یا None.

    None یعنی «دیگر ادامه نده»: یا API کنار گذاشته شد یا جوابِ غیرقابل‌استفاده
    داد. تلاشِ دوباره *همان دسته* را می‌فرستد، نه دسته‌ی بعدی — وگرنه صد IP
    که به ۴۲۹ خوردند بی‌صدا حذف می‌شوند و همان «کشور نامعلوم» برمی‌گردد.
    """
    for _attempt in range(max(1, GEO_MAX_429)):
        try:
            async with session.post(
                GEO_BATCH_API,
                data=json.dumps(chunk),
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 429:
                    ttl = resp.headers.get("X-Ttl")
                    if not _note_429(GEO_BATCH_API, ttl):
                        return None
                    await asyncio.sleep(_wait_for(ttl))
                    continue
                if resp.status != 200:
                    logger.warning(f"⚠️ batch با کد {resp.status} — پشتیبان تک‌به‌تک")
                    return None
                return (
                    await resp.json(content_type=None),
                    resp.headers.get("X-Rl"),
                    resp.headers.get("X-Ttl"),
                )
        except Exception as exc:
            logger.warning(f"⚠️ batch ناموفق ({type(exc).__name__}) — پشتیبان تک‌به‌تک")
            return None
    return None


async def lookup_batch(
    ips: List[str],
    session: aiohttp.ClientSession,
    deadline: float = 0.0,
) -> Dict[str, str]:
    """IP های یکتا → {ip: کد کشور} با ip-api/batch (۱۰۰ IP در هر POST).

    سهمیه ۱۵ POST در دقیقه است و سرور با هدر X-Rl می‌گوید چند تا مانده؛
    وقتی به صفر رسید تا X-Ttl ثانیه *هیچ* درخواستی نباید فرستاده شود، وگرنه
    IP یک ساعت بن می‌شود. پس این‌جا برخلاف بقیه‌ی لایه‌ها موازی‌سازی نداریم:
    ترتیبی و با احترام به هدر.

    IP هایی که جوابشان نیامد در خروجی نیستند؛ تصمیم درباره‌شان (پشتیبان یا
    «نامعلوم») کار فراخوان است.
    """
    found: Dict[str, str] = {}
    if GEO_BATCH_API in _disabled_apis:
        return found

    for chunk in _chunks(ips, GEO_BATCH_SIZE):
        if deadline and time.monotonic() >= deadline:
            logger.warning("⚠️ بودجه‌ی لایه ۶ تمام شد — بقیه‌ی IP ها نامعلوم می‌مانند")
            break
        answer = await _post_batch(chunk, session)
        if answer is None:
            break
        payload, remaining, ttl = answer

        for entry in payload if isinstance(payload, list) else []:
            if not isinstance(entry, dict) or entry.get("status") != "success":
                continue
            ip = str(entry.get("query") or "")
            country = normalize_country(entry.get("countryCode"))
            if ip and country:
                found[ip] = country
                _geo_cache[ip] = country

        # سهمیه ته کشید: صبرِ قبل از POST بعدی اجباری است.
        try:
            if remaining is not None and int(remaining) <= 0:
                await asyncio.sleep(_wait_for(ttl))
        except (TypeError, ValueError):
            pass

    return found


async def check_geo_single(
    config: str,
    session: aiohttp.ClientSession,
) -> Tuple[bool, str, str]:
    """بررسی یک کانفیگ → (معتبر, کشور, دلیل).

    پوسته‌ی سازگاری برای ابزارهای تک‌کانفیگی. مسیر دسته‌ای از این تابع
    استفاده نمی‌کند چون هر فراخوانی‌اش یک resolve و یک کوئری جدا می‌زند —
    همان الگویی که سهمیه‌ی API را می‌سوزاند.
    """
    info = vless.parse(config)
    if info is None or not info.host:
        return False, "", "host خالی"

    ip = await resolve_host(info.host)
    if not ip:
        return False, "", f"resolve ناموفق: {info.host}"

    if is_iran_ip_simple(ip):
        return False, "IR", f"IP ایران: {ip}"

    country = await get_country(ip, session)
    if country is None:
        # نامعلوم: پاس می‌کنیم تا یک قطعی API کل خروجی رو صفر نکنه،
        # ولی در آمار شمرده میشه.
        return True, UNKNOWN, ""

    if country in BLOCKED_COUNTRIES:
        return False, country, f"کشور بلاک: {country} ({ip})"
    return True, country, ""


async def resolve_many(hosts: List[str]) -> Dict[str, str]:
    """میزبان‌های یکتا → {host: ip}. آن‌هایی که resolve نشدند در خروجی نیستند."""
    semaphore = asyncio.Semaphore(max(1, MAX_CONCURRENT_GEO))

    async def one(host: str) -> Tuple[str, Optional[str]]:
        async with semaphore:
            return host, await resolve_host(host)

    results = await asyncio.gather(
        *[one(h) for h in hosts], return_exceptions=True
    )
    resolved: Dict[str, str] = {}
    for item in results:
        if isinstance(item, BaseException):
            continue
        host, ip = item
        if ip:
            resolved[host] = ip
    return resolved


async def lookup_countries(
    ips: List[str],
    session: aiohttp.ClientSession,
    deadline: float = 0.0,
) -> Dict[str, str]:
    """IP های یکتا → {ip: کشور}. اول batch، بعد پشتیبانِ تک‌به‌تک.

    پشتیبان محدود است: هر IP جامانده یک درخواست جدا می‌خواهد و اگر batch
    کلاً از کار افتاده باشد، هزار درخواستِ تک‌به‌تک همان rate-limit را از نو
    می‌سازد. پس فقط تا سقف یک دسته (GEO_BATCH_SIZE) جامانده تلاش می‌شود و
    بقیه «نامعلوم» می‌مانند — «نامعلوم» صادقانه‌تر از عددِ حدسی است.
    """
    known = {ip: _geo_cache[ip] for ip in ips if ip in _geo_cache}
    pending = [ip for ip in ips if ip not in known]
    if not pending:
        return known

    known.update(await lookup_batch(pending, session, deadline))
    missing = [ip for ip in pending if ip not in known]
    if not missing:
        return known

    logger.info(f"   {len(missing)} IP از batch نیامد — تلاش تک‌به‌تک")
    semaphore = asyncio.Semaphore(max(1, MAX_CONCURRENT_GEO))

    async def one(ip: str) -> Tuple[str, Optional[str]]:
        if deadline and time.monotonic() >= deadline:
            return ip, None
        async with semaphore:
            return ip, await get_country(ip, session)

    results = await asyncio.gather(
        *[one(ip) for ip in missing[:GEO_BATCH_SIZE]], return_exceptions=True
    )
    for item in results:
        if isinstance(item, BaseException):
            continue
        ip, country = item
        if country:
            known[ip] = country
    return known


def judge(ip: Optional[str], country: str) -> Tuple[bool, str, str]:
    """حکمِ یک کانفیگ از روی IP و کشورِ *ازقبل‌دانسته* — بدون شبکه.

    جدا نگه داشته شده تا منطق تصمیم بدون سوکت و بدون API تست‌شدنی باشد.
    """
    if not ip:
        return False, "", "resolve ناموفق"
    if is_iran_ip_simple(ip):
        return False, "IR", f"IP ایران: {ip}"
    if not country:
        # نامعلوم پاس می‌شود تا یک قطعی API کل خروجی را صفر نکند، ولی در
        # آمار شمرده می‌شود و برچسب کشور نمی‌گیرد.
        return True, UNKNOWN, ""
    if country in BLOCKED_COUNTRIES:
        return False, country, f"کشور بلاک: {country} ({ip})"
    return True, country, ""


async def check_geo_batch(configs: List[str]) -> Tuple[List[Tuple[str, str]], dict]:
    """بررسی دسته‌ای → (لیست (config, country), آمار).

    سه مرحله، و هیچ‌کدام به‌ازای هر کانفیگ تکرار نمی‌شود:
      ۱. میزبان‌های یکتا resolve می‌شوند.
      ۲. IP های یکتا (منهای آن‌هایی که با پیشوند، ایران تشخیص داده شدند)
         یک بار کوئری می‌شوند.
      ۳. حکمِ هر کانفیگ از نقشه‌ها ساخته می‌شود — بی‌شبکه، پس ارزان.
    """
    valid: List[Tuple[str, str]] = []
    failed = 0
    countries: Dict[str, int] = {}
    reasons: Dict[str, int] = {}

    budget = max(0, GEO_BUDGET_SEC)
    deadline = time.monotonic() + budget if budget else 0.0

    host_of: Dict[str, str] = {}
    for cfg in configs:
        info = vless.parse(cfg)
        if info and info.host:
            host_of[cfg] = info.host

    hosts = sorted({h for h in host_of.values()})
    logger.info(
        f"🌍 بررسی Geo {len(configs)} کانفیگ روی {len(hosts)} میزبان یکتا..."
    )

    ip_of = await resolve_many(hosts)
    # IP هایی که با پیشوند ایران تشخیص داده شدند سهمیه‌ی API نمی‌خورند.
    unique_ips = sorted({
        ip for ip in ip_of.values() if not is_iran_ip_simple(ip)
    })
    logger.info(f"   {len(ip_of)} میزبان resolve شد | {len(unique_ips)} IP یکتا")

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT_GEO)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        country_of = await lookup_countries(unique_ips, session, deadline)

    for cfg in configs:
        if cfg not in host_of:
            failed += 1
            reasons["host خالی"] = reasons.get("host خالی", 0) + 1
            continue
        ip = ip_of.get(host_of[cfg])
        ok, country, reason = judge(ip, country_of.get(ip or "", ""))
        if ok:
            valid.append((cfg, country))
            countries[country] = countries.get(country, 0) + 1
        else:
            failed += 1
            key = reason.split(":")[0] or "نامشخص"
            reasons[key] = reasons.get(key, 0) + 1

    unknown = countries.get(UNKNOWN, 0)
    stats = {
        "total": len(configs),
        "passed": len(valid),
        "failed": failed,
        "unique_hosts": len(hosts),
        "unique_ips": len(unique_ips),
        "geo_known_ips": len(country_of),
        "unknown_country": unknown,
        "countries": countries,
        "fail_reasons": reasons,
    }
    if unknown and valid and unknown / len(valid) > 0.5:
        logger.warning(
            f"⚠️ کشور {unknown} کانفیگ نامعلوم موند (API محدود شده) — "
            "فیلتر جغرافیایی این اجرا قابل اعتماد نیست"
        )
    logger.info(
        f"لایه ۶ (Geo): {stats['passed']}/{stats['total']} | "
        f"{len(countries)} کشور | نامعلوم: {unknown}"
    )
    return valid, stats
