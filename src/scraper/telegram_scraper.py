"""
Scraper کانال‌های تلگرام
باگ ۲ رفع: استفاده از StringSession به جای فایل session
"""
import asyncio
from typing import List

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.config import (
    TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION,
    TELEGRAM_CHANNELS, MAX_PER_SOURCE,
)
from src.logger import get_logger

logger = get_logger("telegram_scraper")

# صبر کردن پای یک FloodWait طولانی کل بودجه‌ی ۵۵ دقیقه‌ای job رو می‌خوره.
MAX_FLOOD_WAIT_SEC = 60


def extract_vless(text: str) -> List[str]:
    """لینک‌های vless داخل متن پیام (پیام‌های Base64 هم پوشش داده میشن)."""
    return vless.extract_configs(text or "")


async def scrape_telegram() -> List[str]:
    """
    باگ ۲ رفع: StringSession برای محیط CI/CD
    اگه TELEGRAM_SESSION تنظیم نشده، رد میشه
    """
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH or not TELEGRAM_SESSION:
        logger.warning(
            "⚠️ TELEGRAM_SESSION تنظیم نشده - scrape تلگرام رد شد\n"
            "   برای فعال‌سازی: یه بار روی سیستم خودت session بساز و\n"
            "   مقدار StringSession رو در TELEGRAM_SESSION ذخیره کن"
        )
        return []

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.errors import ChannelPrivateError, FloodWaitError

        all_configs: List[str] = []

        # ✅ باگ ۲ رفع: StringSession — نیاز به تعامل ندارد
        async with TelegramClient(
            StringSession(TELEGRAM_SESSION),
            int(TELEGRAM_API_ID),
            TELEGRAM_API_HASH,
        ) as client:
            logger.info("✅ تلگرام متصل شد")

            for channel in TELEGRAM_CHANNELS:
                try:
                    entity = await client.get_entity(channel)
                    configs = []
                    async for msg in client.iter_messages(entity, limit=100):
                        text = msg.text or ""
                        found = extract_vless(text)
                        configs.extend(found)
                        if len(configs) >= MAX_PER_SOURCE:
                            break
                    logger.info(f"  📡 @{channel}: {len(configs)} کانفیگ")
                    all_configs.extend(configs)
                    await asyncio.sleep(1.5)
                except ChannelPrivateError:
                    logger.warning(f"  ⚠️ خصوصی: @{channel}")
                except FloodWaitError as e:
                    if e.seconds > MAX_FLOOD_WAIT_SEC:
                        logger.warning(
                            f"  ⏳ flood {e.seconds}s — از این کانال رد شدیم"
                        )
                        continue
                    logger.warning(f"  ⏳ flood {e.seconds}s")
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    logger.debug(f"  خطا @{channel}: {e}")

        logger.info(f"✅ تلگرام: {len(all_configs)} کانفیگ از {len(TELEGRAM_CHANNELS)} کانال")
        return all_configs

    except Exception as e:
        logger.error(f"❌ خطای تلگرام: {e}")
        return []


async def generate_session():
    """
    یه بار روی سیستم خودت اجرا کن تا StringSession بسازی
    مقدار رو در TELEGRAM_SESSION ذخیره کن
    """
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = input("API ID: ")
    api_hash = input("API Hash: ")

    async with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        session_str = client.session.save()
        print(f"\n✅ StringSession:\n{session_str}\n")
        print("این مقدار رو در Secret TELEGRAM_SESSION ذخیره کن")


if __name__ == "__main__":
    asyncio.run(generate_session())
