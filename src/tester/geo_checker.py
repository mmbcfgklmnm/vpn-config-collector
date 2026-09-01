"""
لایه ۵: بررسی موقعیت جغرافیایی سرور
سرور نباید در کشورهای بلاک‌شده باشه (پیش‌فرض: IR, KP)

اصلاحات نسبت به نسخه‌ی قبلی:
  ۱. BLOCKED_COUNTRIES از config خوانده میشه (قبلاً RU و CN هم داخلش بود و
     حجم زیادی از سرورهای سالم رو حذف می‌کرد).
  ۲. پیشوندهای غلط «IP ایران» حذف شدند: 192.99 (OVH کانادا)، 193.0 (RIPE NCC
     هلند)، 91.108 (تلگرام) و 185.1/2/3/10 که فضای IXP اروپاست. این‌ها
     سرورهای سالم رو به‌عنوان ایران رد می‌کردند.
  ۳. کد کشور اگر ۲ حرفی نبود دیگر با upper()[:2] بریده نمیشه
     (Netherlands → "NE" که هیچ کشوری نیست)؛ از جدول نام استفاده میشه.
  ۴. وقتی API با 429 جواب بده همان API برای بقیه‌ی اجرا کنار گذاشته میشه و
     تعداد «کشور نامعلوم» در آمار گزارش میشه — قبلاً rate-limit بی‌صدا به
     «همه پاس» تبدیل می‌شد.
  ۵. IP ها یکتا resolve و query میشن، پس مصرف سهمیه‌ی API خیلی کمتره.
"""
import asyncio
import ipaddress
import socket
from typing import Dict, List, Optional, Set, Tuple

import aiohttp

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.config import BLOCKED_COUNTRIES, MAX_CONCURRENT_GEO
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

# GeoIP API های رایگان — به ترتیب تلاش میشن.
GEO_APIS = [
    "https://api.country.is/{ip}",
    "https://ipinfo.io/{ip}/json",
    "https://ip-api.com/json/{ip}?fields=countryCode",
]

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
    """کد کشور از IP. None یعنی هیچ API جواب قابل‌استفاده نداد."""
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
                    # سهمیه تموم شده؛ ادامه دادن فقط وقت تلف می‌کنه.
                    _disabled_apis.add(api_url)
                    logger.warning(f"⚠️ rate-limit روی {api_url} — کنار گذاشته شد")
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


async def check_geo_single(
    config: str,
    session: aiohttp.ClientSession,
) -> Tuple[bool, str, str]:
    """بررسی یک کانفیگ → (معتبر, کشور, دلیل)."""
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


async def check_geo_batch(configs: List[str]) -> Tuple[List[Tuple[str, str]], dict]:
    """بررسی دسته‌ای → (لیست (config, country), آمار)."""
    valid: List[Tuple[str, str]] = []
    failed = 0
    countries: Dict[str, int] = {}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_GEO)

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT_GEO)
    timeout = aiohttp.ClientTimeout(total=15)

    logger.info(f"🌍 بررسی Geo {len(configs)} کانفیگ...")

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        async def bounded(cfg: str):
            async with semaphore:
                return cfg, await check_geo_single(cfg, session)

        results = await asyncio.gather(
            *[bounded(c) for c in configs],
            return_exceptions=True,
        )

    for item in results:
        if isinstance(item, BaseException):
            failed += 1
            continue
        cfg, (ok, country, _reason) = item
        if ok:
            valid.append((cfg, country))
            countries[country] = countries.get(country, 0) + 1
        else:
            failed += 1

    unknown = countries.get(UNKNOWN, 0)
    stats = {
        "total": len(configs),
        "passed": len(valid),
        "failed": failed,
        "unknown_country": unknown,
        "countries": countries,
    }
    if unknown and valid and unknown / len(valid) > 0.5:
        logger.warning(
            f"⚠️ کشور {unknown} کانفیگ نامعلوم موند (API محدود شده) — "
            "فیلتر جغرافیایی این اجرا قابل اعتماد نیست"
        )
    logger.info(
        f"لایه ۵ (Geo): {stats['passed']}/{stats['total']} | کشورها: {countries}"
    )
    return valid, stats
