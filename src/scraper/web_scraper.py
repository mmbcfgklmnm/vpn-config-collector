"""
Scraper URL های مستقیم (subscription های عمومی).

باگ base64: نسخه‌ی قبلی فقط space و \\r رو حذف می‌کرد، پس \\n داخل متن
می‌موند و طولِ رشته برای محاسبه‌ی padding غلط درمی‌آمد — تقریباً همه‌ی
subscription های چندخطیِ Base64 بی‌صدا صفر کانفیگ می‌دادند.
حالا استخراج از src.vless.extract_configs می‌آید که whitespace رو کامل
حذف می‌کنه و urlsafe رو هم پوشش می‌ده.

TLS هم دیگه با ssl=False غیرفعال نیست: این‌ها URL های عمومی HTTPS هستند و
دلیلی برای پذیرفتن گواهی جعلی وجود نداره.
"""
import asyncio
from typing import List

import aiohttp

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.config import DIRECT_URLS, MAX_PER_SOURCE
from src.logger import get_logger

logger = get_logger("web_scraper")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
}

MAX_CONCURRENT = 20


def extract_vless(text: str) -> List[str]:
    """استخراج لینک‌های vless از متن خام یا Base64."""
    return vless.extract_configs(text, limit=MAX_PER_SOURCE)


async def fetch_url(session: aiohttp.ClientSession, url: str) -> List[str]:
    try:
        async with session.get(url, headers=HEADERS, allow_redirects=True) as resp:
            if resp.status != 200:
                logger.warning(f"⚠️ HTTP {resp.status}: {url[:60]}")
                return []
            text = await resp.text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        logger.debug(f"خطا {url[:40]}: {exc}")
        return []

    found = extract_vless(text)
    logger.info(f"🌐 {url[:60]}... → {len(found)} کانفیگ")
    return found


async def scrape_web() -> List[str]:
    all_configs: List[str] = []
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT)
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        results = await asyncio.gather(
            *[fetch_url(session, url) for url in DIRECT_URLS],
            return_exceptions=True,
        )

    failures = 0
    for item in results:
        if isinstance(item, list):
            all_configs.extend(item)
        else:
            failures += 1

    if failures:
        logger.warning(f"⚠️ {failures} از {len(DIRECT_URLS)} منبع وب خطا داد")
    logger.info(f"✅ وب: {len(all_configs)} کانفیگ از {len(DIRECT_URLS)} URL")
    return all_configs
