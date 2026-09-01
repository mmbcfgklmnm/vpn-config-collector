"""
Publisher — ارسال کانفیگ‌ها به کانال تلگرام.

سه اصلاح:
  ۱. اسم کانفیگ (که از منابع عمومی scrape شده) مستقیم داخل متن Markdown
     درج می‌شد؛ یک `_` یا `*` در اسم باعث خطای 400 «Can't parse entities»
     می‌شد و *کل* پیام ارسال نمی‌شد. الان از src.tg_md رد میشه.
  ۲. خروجی send نادیده گرفته می‌شد و لاگ «ارسال کامل» حتی وقتی همه‌ی
     پیام‌ها fail شده بودند چاپ می‌شد.
  ۳. تشخیص Reality با جست‌وجوی رشته در کل لینک بود، پس کانفیگ TLS با کلمه‌ی
     reality در اسمش هم Reality شمرده می‌شد.
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError, TimedOut

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import tg_md, vless
from src.config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID,
    CONFIGS_PER_TG_MESSAGE, TG_SAFE_MSG_LEN, SUB_URL,
)
from src.logger import get_logger

logger = get_logger("publisher")

SECURITY_ICON = {"Reality": "🔐 Reality", "TLS": "🔒 TLS", "Other": "🔑 Other"}


def utc_now(fmt: str = "%Y-%m-%d %H:%M UTC") -> str:
    """utcnow() از پایتون ۳.۱۲ deprecated است و tz-naive برمی‌گرداند."""
    return datetime.now(timezone.utc).strftime(fmt)


def get_name(config: str) -> str:
    return tg_md.strip_md(vless.get_name(config, 35)) or "بدون نام"


def get_security(config: str) -> str:
    return SECURITY_ICON[vless.get_security_label(config)]


def build_header(configs: List[str], stats: Dict) -> str:
    labels = [vless.get_security_label(c) for c in configs]
    reality = labels.count("Reality")
    tls = labels.count("TLS")
    other = len(labels) - reality - tls

    pipeline = stats.get("pipeline", {})
    raw = pipeline.get("layer1_format", {}).get("total", 0)

    lines = [
        "🛡️ *VPN Config Update*",
        "─" * 28,
        f"🕐 {utc_now()}",
        f"📊 کل معتبر: *{len(configs)}* کانفیگ",
        "",
        "📡 پروتکل:",
        f"  🟢 VLESS Reality: {reality}",
        f"  🟢 VLESS TLS: {tls}",
    ]
    if other:
        lines.append(f"  ⚪ سایر: {other}")
    lines += [
        "",
        f"📥 جمع‌آوری شده: {raw}",
        "─" * 28,
        "⬇️ کانفیگ‌ها در پیام‌های بعدی",
    ]
    if SUB_URL:
        lines += ["", "🔗 *Subscription:*", tg_md.code(SUB_URL)]
    return "\n".join(lines)


def build_batch_msg(configs: List[str], index: int, total: int) -> str:
    lines = [f"📦 *بسته {index}/{total}*\n"]
    for cfg in configs:
        lines.append(f"{get_security(cfg)} — {get_name(cfg)}\n{tg_md.code(cfg)}\n")
    return tg_md.truncate("\n".join(lines), TG_SAFE_MSG_LEN)


class Publisher:
    def __init__(self):
        self.bot: Optional[Bot] = None

    async def connect(self) -> bool:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
            logger.error("❌ TELEGRAM_BOT_TOKEN یا TELEGRAM_CHANNEL_ID نیست")
            return False
        try:
            self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
            me = await self.bot.get_me()
            logger.info(f"✅ ربات متصل: @{me.username}")
            return True
        except TelegramError as exc:
            logger.error(f"❌ اتصال ربات: {exc}")
            return False

    async def send(self, text: str, retries: int = 3) -> bool:
        if self.bot is None:
            logger.error("❌ ربات متصل نیست")
            return False
        for attempt in range(retries):
            try:
                await self.bot.send_message(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
                return True
            except RetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 1)
            except TimedOut:
                await asyncio.sleep(5)
            except TelegramError as exc:
                logger.error(f"❌ ارسال (تلاش {attempt + 1}): {exc}")
                if attempt < retries - 1:
                    await asyncio.sleep(3)
        return False

    async def publish(self, configs: List[str], stats: Dict) -> bool:
        if not configs:
            logger.warning("⚠️ کانفیگ برای ارسال نیست")
            return False

        if not await self.connect():
            return False

        sent = 0
        failed = 0

        if await self.send(build_header(configs, stats)):
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(2)

        batches = [
            configs[i:i + CONFIGS_PER_TG_MESSAGE]
            for i in range(0, len(configs), CONFIGS_PER_TG_MESSAGE)
        ]
        for index, batch in enumerate(batches, 1):
            ok = await self.send(build_batch_msg(batch, index, len(batches)))
            if not ok:
                # بسته‌ی کامل رد شد؛ تک‌تک می‌فرستیم تا همه‌ی کانفیگ‌ها
                # به‌خاطر یک قلم مشکل‌دار از دست نرن.
                for cfg in batch:
                    if await self.send(f"{get_security(cfg)}\n{tg_md.code(cfg)}"):
                        sent += 1
                    else:
                        failed += 1
                    await asyncio.sleep(0.5)
            else:
                sent += 1
            await asyncio.sleep(2)
            logger.info(f"  📤 بسته {index}/{len(batches)}: {'ok' if ok else 'تک‌تک'}")

        await self.send(
            f"✅ *ارسال کامل شد*\n"
            f"📊 {len(configs)} کانفیگ\n"
            f"🔄 آپدیت بعدی: ۱ ساعت دیگه\n\n"
            f"#vless #vpn #v2ray #فیلترشکن"
        )

        if failed:
            logger.error(f"❌ {failed} پیام ارسال نشد ({sent} موفق)")
            return False
        logger.info(f"✅ ارسال کامل: {len(configs)} کانفیگ در {sent} پیام")
        return True

    async def send_error(self, error: str) -> None:
        """گزارش خطا به کانال. اگه ربات وصل نیست اول وصل میشه."""
        try:
            if self.bot is None and not await self.connect():
                return
            await self.send(
                f"⚠️ *خطا در آپدیت*\n"
                f"🕐 {utc_now('%H:%M UTC')}\n"
                f"❌ {tg_md.strip_md(error, 200)}"
            )
        except Exception:
            pass
