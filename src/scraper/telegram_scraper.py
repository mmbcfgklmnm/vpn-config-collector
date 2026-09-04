"""Scraper کانال‌های تلگرام — بی‌نیاز از حساب، با Telethon به‌عنوان مسیر دوم.

شکایت کاربر: «منابع را به گیت‌هاب محدود نکن؛ کانال‌های تلگرام را هم اضافه
کن.» نسخه‌ی قبلی *ظاهراً* تلگرام داشت ولی هیچ‌وقت اجرا نمی‌شد: اولین شرطش
TELEGRAM_SESSION بود و در health.json هیچ رکوردی با kind=telegram نبود —
یعنی ده کانالِ فهرست‌شده در عمل صفر کانفیگ می‌دادند و تمام خروجی از وب و
گیت‌هاب می‌آمد.

مسیر اصلی حالا صفحه‌ی پیش‌نمایش عمومی است: `https://t.me/s/<channel>` بیست
پیام آخر را HTML می‌دهد و با `?before=<id>` به عقب ورق می‌خورد. نه حساب
لازم دارد، نه API key، و هیچ چیزی هم فرستاده نمی‌شود — فقط GET.

سه تله‌ی HTML که رعایت نکردنشان خروجی را بی‌صدا صفر یا خراب می‌کند:
  ۱. `&` در HTML به شکل `&amp;` است. بدون unescape هر کانفیگ به‌صورت
     `...?security=tls&amp;type=tcp` استخراج می‌شود و در کلاینت کار نمی‌کند.
  ۲. چند کانفیگ در یک پیام با `<br/>` جدا می‌شوند، نه با خط جدید. بدون
     تبدیل کردنش دو کانفیگ به هم می‌چسبند و هر دو از دست می‌روند.
  ۳. کانالِ بسته/خصوصی/گروه هم ۲۰۰ برمی‌گرداند (صفحه‌ی «Contact @x»). پس
     مرده بودن با شمردن پیام‌ها تشخیص داده می‌شود، نه با status code.

اندازه‌گیری روی خودِ فهرست (یک صفحه از هر کانال، بی ورق زدن): از ۵۷ کانالِ
آزموده ۲۹ کانال کانفیگ دادند، از ۱۴۳ کانفیگ (proxy_mtm) تا ۳. پنج کانالِ
فهرست قبلی — V2RAYCONFIGSPOOL و MahsaNetConfigTopic و ShadowException و
freev2ray و v2rayNG_Backup — پیش‌نمایش عمومی ندارند و حذف شدند.

امنیت: در این مسیر هیچ اعتبارنامه‌ای دخیل نیست. TELEGRAM_SESSION و
API_ID/API_HASH فقط به Telethon داده می‌شوند، هیچ‌جا لاگ نمی‌شوند، و اگر
تنظیم نشده باشند مسیر دوم بی‌صدا رد می‌شود — نبودشان خطا نیست.
"""
import asyncio
import html
import re
from typing import List, Optional, Set, Tuple

import aiohttp

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.config import (
    TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION,
    TELEGRAM_CHANNELS, TELEGRAM_PAGES, MAX_PER_SOURCE,
)
from src.health import record as record_health
from src.logger import get_logger

logger = get_logger("telegram_scraper")

PREVIEW_URL = "https://t.me/s/{channel}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
}

# t.me سهمیه‌ی اعلام‌شده ندارد؛ این دو عدد محافظه‌کارانه‌اند تا ۲۹ کانال ×
# چند صفحه به throttle نخورد و فاز جمع‌آوری هم طول نکشد.
MAX_CONCURRENT_TELEGRAM = 6
PAGE_PAUSE_SEC = 0.4

# صبر کردن پای یک FloodWait طولانی کل بودجه‌ی ۵۵ دقیقه‌ای job رو می‌خوره.
MAX_FLOOD_WAIT_SEC = 60

# متن هر پیام داخل div ای با کلاس js-message_text است. با split (نه
# match تا `</div>`) کار می‌کنیم چون داخل متن پیام هم div تو‌رفته هست و
# شمردن تگ‌های بسته شکننده است.
_MSG_SPLIT = re.compile(r'class="[^"]*js-message_text[^"]*"', re.I)
# انتهای متن پیام: امضا/زمان، پیش‌نمایش لینک، یا نقلِ پیام بعدی. اگر بریده
# نشود، لینکِ داخل پیش‌نمایش هم به‌عنوان کانفیگ شمرده می‌شود.
_MSG_END = re.compile(
    r'class="[^"]*tgme_widget_message_(?:footer|reply|link_preview|meta)', re.I
)
_BR = re.compile(r"<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_POST_ID = re.compile(r'data-post="[^"]*/(\d+)"')


def message_texts(page: str) -> List[str]:
    """HTML صفحه‌ی پیش‌نمایش → متن خامِ هر پیام.

    جدا کردن پیام‌ها (به‌جای استخراج از کل صفحه) برای پیام‌های Base64 لازم
    است: `vless.extract_configs` فقط وقتی decode را امتحان می‌کند که در
    *همان* متن هیچ `vless://` ای نباشد.
    """
    texts: List[str] = []
    for part in _MSG_SPLIT.split(page or "")[1:]:
        # split وسطِ تگِ باز افتاد، پس تا اولین `>` باقی‌مانده‌ی همان تگ است
        # (`dir="auto">`). نبریدنش برای پیام‌های Base64 کشنده است: آن چند
        # حرف به بلوک می‌چسبند و decode را خراب می‌کنند.
        head = part.find(">")
        if head < 0:
            continue
        part = part[head + 1:]
        end = _MSG_END.search(part)
        if end:
            part = part[:end.start()]
        # تگ → خط جدید: مرزِ تگ یک مرزِ واقعی است و چسباندنشان کانفیگ
        # می‌سازد که در هیچ کلاینتی باز نمی‌شود.
        text = html.unescape(_TAG.sub("\n", _BR.sub("\n", part))).strip()
        if text:
            texts.append(text)
    return texts


def message_ids(page: str) -> List[int]:
    """شماره‌ی پیام‌های صفحه — برای ورق زدن با ?before=."""
    return [int(mid) for mid in _POST_ID.findall(page or "")]


def extract_vless(text: str) -> List[str]:
    """لینک‌های vless داخل متن پیام (پیام‌های Base64 هم پوشش داده میشن)."""
    return vless.extract_configs(text or "")


def configs_in_page(page: str) -> List[str]:
    """کانفیگ‌های یکتای یک صفحه، به ترتیب دیده شدن."""
    seen: Set[str] = set()
    found: List[str] = []
    for text in message_texts(page):
        for config in extract_vless(text):
            if config not in seen:
                seen.add(config)
                found.append(config)
    return found


async def fetch_page(
    session: aiohttp.ClientSession, url: str
) -> Tuple[Optional[str], str]:
    """(HTML, خطا). خطای غیرخالی یعنی صفحه نیامد."""
    try:
        async with session.get(url, headers=HEADERS, allow_redirects=True) as resp:
            if resp.status != 200:
                return None, f"HTTP {resp.status}"
            return await resp.text(encoding="utf-8", errors="ignore"), ""
    except Exception as exc:
        return None, type(exc).__name__


async def fetch_channel(session: aiohttp.ClientSession, channel: str) -> List[str]:
    """یک کانال، تا TELEGRAM_PAGES صفحه به عقب → کانفیگ‌های یکتا."""
    seen: Set[str] = set()
    found: List[str] = []
    before = 0
    pages_read = 0
    error = ""

    for _page in range(max(1, TELEGRAM_PAGES)):
        url = PREVIEW_URL.format(channel=channel)
        if before:
            url = f"{url}?before={before}"
        page, error = await fetch_page(session, url)
        if page is None:
            break

        texts = message_texts(page)
        if not texts:
            # ۲۰۰ ولی بی‌پیام: یا کانال عمومی نیست یا به انتهای آن رسیدیم.
            error = "پیش‌نمایش عمومی ندارد" if not pages_read else ""
            break
        pages_read += 1

        for text in texts:
            for config in extract_vless(text):
                if config not in seen:
                    seen.add(config)
                    found.append(config)
        if len(found) >= MAX_PER_SOURCE:
            break

        ids = message_ids(page)
        oldest = min(ids) if ids else 0
        if not oldest or (before and oldest >= before):
            break        # ورق نخورد: انتهای کانال یا صفحه‌ی تکراری
        before = oldest
        await asyncio.sleep(PAGE_PAUSE_SEC)

    found = found[:MAX_PER_SOURCE]
    # خطا فقط وقتی گزارش می‌شود که دست‌خالی مانده باشیم: کانالی که ۱۰۰
    # کانفیگ داد و صفحه‌ی سومش ۴۲۹ خورد، منبعِ مرده نیست.
    record_health("telegram", channel, len(found), "" if found else error)
    if not found:
        logger.warning(f"  ⚠️ @{channel}: {error or 'صفر کانفیگ'}")
    else:
        logger.info(f"  📡 @{channel}: {len(found)} کانفیگ ({pages_read} صفحه)")
    return found


async def scrape_telegram_web() -> List[str]:
    """مسیر اصلی: صفحه‌ی پیش‌نمایش عمومی، بدون هیچ اعتبارنامه‌ای."""
    if not TELEGRAM_CHANNELS:
        return []

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT_TELEGRAM)
    timeout = aiohttp.ClientTimeout(total=25)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TELEGRAM)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        async def one(channel: str) -> List[str]:
            async with semaphore:
                return await fetch_channel(session, channel)

        results = await asyncio.gather(
            *[one(channel) for channel in TELEGRAM_CHANNELS],
            return_exceptions=True,
        )

    configs: List[str] = []
    dead = 0
    for item in results:
        if isinstance(item, list) and item:
            configs.extend(item)
        else:
            dead += 1
    if dead:
        logger.warning(f"⚠️ {dead} از {len(TELEGRAM_CHANNELS)} کانال چیزی نداد")
    return configs


async def scrape_telegram_api() -> List[str]:
    """مسیر دوم (اختیاری): Telethon با StringSession.

    برتری‌اش نسبت به پیش‌نمایش وب: محدود به ۲۰ پیام در صفحه نیست و متن
    پیام‌های طولانی را بریده نمی‌بیند. ولی به حساب نیاز دارد، پس شرطِ
    اجرا نیست — اضافه‌ی آن است.
    """
    all_configs: List[str] = []
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.errors import ChannelPrivateError, FloodWaitError

        async with TelegramClient(
            StringSession(TELEGRAM_SESSION),
            int(TELEGRAM_API_ID),
            TELEGRAM_API_HASH,
        ) as client:
            logger.info("✅ تلگرام (Telethon) متصل شد")

            for channel in TELEGRAM_CHANNELS:
                try:
                    entity = await client.get_entity(channel)
                    configs: List[str] = []
                    async for msg in client.iter_messages(entity, limit=200):
                        configs.extend(extract_vless(msg.text or ""))
                        if len(configs) >= MAX_PER_SOURCE:
                            break
                    logger.info(f"  📡 @{channel} (api): {len(configs)} کانفیگ")
                    record_health("telegram", f"{channel}#api", len(configs))
                    all_configs.extend(configs)
                    await asyncio.sleep(1.5)
                except ChannelPrivateError:
                    record_health("telegram", f"{channel}#api", 0, "کانال خصوصی شد")
                except FloodWaitError as exc:
                    record_health(
                        "telegram", f"{channel}#api", 0, f"flood {exc.seconds}s"
                    )
                    if exc.seconds > MAX_FLOOD_WAIT_SEC:
                        logger.warning(f"  ⏳ flood {exc.seconds}s — رد شدیم")
                        continue
                    await asyncio.sleep(exc.seconds)
                except Exception as exc:
                    record_health("telegram", f"{channel}#api", 0, type(exc).__name__)
    except Exception as exc:
        # هیچ‌وقت مقدار session/api_hash در پیام خطا نمی‌آید.
        logger.error(f"❌ خطای Telethon: {type(exc).__name__}")
    return all_configs


async def scrape_telegram() -> List[str]:
    """پیش‌نمایش وب برای همه‌ی کانال‌ها + Telethon اگر session تنظیم شده باشد."""
    configs = await scrape_telegram_web()
    if TELEGRAM_API_ID and TELEGRAM_API_HASH and TELEGRAM_SESSION:
        configs.extend(await scrape_telegram_api())
    else:
        logger.info("   Telethon تنظیم نشده — فقط پیش‌نمایش وب (کافی است)")
    logger.info(
        f"✅ تلگرام: {len(configs)} کانفیگ از {len(TELEGRAM_CHANNELS)} کانال"
    )
    return configs


async def generate_session():
    """یه بار روی سیستم خودت اجرا کن تا StringSession بسازی.

    خروجی را در Secret تلگرام (TELEGRAM_SESSION) بگذار، نه در فایل و نه در
    لاگ: این رشته دسترسی کامل به حساب می‌دهد.
    """
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = input("API ID: ")
    api_hash = input("API Hash: ")

    async with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        print("\n✅ StringSession ساخته شد — در Secret ذخیره کن:\n")
        print(client.session.save())


if __name__ == "__main__":
    asyncio.run(generate_session())
