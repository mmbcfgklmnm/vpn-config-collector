"""Publisher — انتشار کانفیگ‌ها در کانال تلگرام.

قالب انتشار (درخواست کاربر)
───────────────────────────
هر دوره ۱۰ پیام *مستقل*، هر پیام یک کانفیگ با مشخصات کامل، و پیام یازدهم
لینک subscription. قبلاً ۱۰ کانفیگ در *یک* پیام فرستاده می‌شد و کل پول
(~۷۳۸ کانفیگ) در ~۷۶ پیام در ساعت — که هم کانال را می‌ترکاند و هم کیفیت
پیدا کردن یک کانفیگ سالم را برای کاربر صفر می‌کرد.

چرخش (rotation)
───────────────
با انتشار هر ۵ دقیقه، بدون حافظه همان ۱۰ کانفیگ سریع‌ترِ ثابت تکرار می‌شد.
PUBLISH_STATE_FILE نگه می‌دارد هر کانفیگ در کدام دوره پست شده و
PUBLISH_COOLDOWN دوره بعد اجازه‌ی تکرار می‌دهد؛ با ۱۰ کانفیگ در هر دوره و
cooldown=۶، در یک ساعت ~۱۲۰ کانفیگ متفاوت دیده می‌شود.

اولویت انتخاب: اول کانفیگ‌های تأییدشده از ایران (برچسب IR در fragment)،
بعد بقیه به ترتیب تأخیر تونل.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError, TimedOut

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import tg_md, vless
from src.config import (
    HTTP_TEST_ROUNDS, PUBLISH_COOLDOWN, PUBLISH_COUNT, PUBLISH_INTERVAL_MIN,
    PUBLISH_INTRO, PUBLISH_MSG_GAP_SEC, PUBLISH_STATE_FILE, SUB_B64_URL,
    SUB_IRAN_URL, SUB_MIRROR_URL, SUB_URL, TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHANNEL_ID,
)
from src.logger import get_logger
from src.publisher import renderer

logger = get_logger("publisher")


def utc_now(fmt: str = "%Y-%m-%d %H:%M UTC") -> str:
    """utcnow() از پایتون ۳.۱۲ deprecated است و tz-naive برمی‌گرداند."""
    return datetime.now(timezone.utc).strftime(fmt)


# ─── وضعیت چرخش ───────────────────────────────────────────

def load_state() -> Dict:
    try:
        with open(PUBLISH_STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"cycle": 0, "posted": {}}
    if not isinstance(data, dict):
        return {"cycle": 0, "posted": {}}
    data.setdefault("cycle", 0)
    posted = data.get("posted")
    data["posted"] = posted if isinstance(posted, dict) else {}
    return data


def save_state(state: Dict) -> None:
    directory = os.path.dirname(PUBLISH_STATE_FILE) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        tmp = f"{PUBLISH_STATE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, PUBLISH_STATE_FILE)
    except OSError as exc:
        # نبودِ حافظه‌ی چرخش انتشار را متوقف نمی‌کند، فقط تکراری‌تر می‌کند.
        logger.warning(f"⚠️ ذخیره‌ی وضعیت انتشار ناموفق: {exc}")


def rank_key(config: str) -> Tuple[int, float]:
    """کلید مرتب‌سازی: تأییدشده‌ی ایران اول، بعد کم‌تأخیرترین."""
    iran = vless.get_iran_ms(config)
    latency = vless.get_latency_ms(config)
    return (0 if iran > 0 else 1, latency)


def select_for_publish(
    configs: List[str], state: Dict, count: int = 0
) -> List[str]:
    """انتخاب کانفیگ‌های این دوره با رعایت cooldown.

    اگر بعد از cooldown چیزی نماند (پول کوچک‌تر از count×cooldown)، سراغ
    قدیمی‌ترین پست‌شده‌ها می‌رویم — کانال خالی نمی‌ماند.
    """
    count = count or PUBLISH_COUNT
    if not configs:
        return []
    cycle = int(state.get("cycle", 0))
    posted: Dict[str, int] = state.get("posted", {})

    ordered = sorted(dict.fromkeys(configs), key=rank_key)
    fresh: List[str] = []
    stale: List[Tuple[int, str]] = []
    for cfg in ordered:
        last = posted.get(vless.short_id(cfg))
        if last is None or cycle - int(last) >= max(1, PUBLISH_COOLDOWN):
            fresh.append(cfg)
            if len(fresh) >= count:
                return fresh
        else:
            stale.append((int(last), cfg))

    stale.sort(key=lambda item: item[0])
    fresh.extend(cfg for _, cfg in stale[: count - len(fresh)])
    return fresh[:count]


def mark_published(state: Dict, configs: List[str]) -> Dict:
    """ثبت اینکه این کانفیگ‌ها در دوره‌ی جاری پست شدند."""
    cycle = int(state.get("cycle", 0)) + 1
    posted: Dict[str, int] = dict(state.get("posted", {}))
    for cfg in configs:
        posted[vless.short_id(cfg)] = cycle
    # هرس: شناسه‌هایی که خیلی قدیمی‌اند دیگر روی تصمیم اثر ندارند و فایل را
    # بی‌دلیل بزرگ می‌کنند.
    horizon = cycle - max(1, PUBLISH_COOLDOWN) * 4
    posted = {k: v for k, v in posted.items() if v >= horizon}
    return {
        "cycle": cycle,
        "posted": posted,
        "last_publish": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_count": len(configs),
    }


# ─── متن پیام‌ها ───────────────────────────────────────────

def build_sub_message(total: int, iran_count: int = 0) -> str:
    """پیام یازدهم: لینک subscription — همان چیزی که کاربر خواست زیر آخرین پست.

    چند لینک می‌دهیم چون کلاینت‌ها یکسان نیستند: v2rayNG/NekoBox نسخه‌های
    قدیمی فهرست متنی را نمی‌خوانند و فقط Base64 را قبول می‌کنند.
    """
    lines = [
        "📡 *لینک اشتراک (Subscription)*",
        "─" * 26,
        "با این لینک همه‌ی کانفیگ‌ها یک‌جا و همیشه به‌روز در کلاینت شما می‌آید.",
        "",
        f"📦 کانفیگ در این لینک: *{total}*",
    ]
    if iran_count:
        lines.append(f"🇮🇷 تأییدشده از ایران: *{iran_count}*")
    lines += ["", "🔗 *لینک اصلی:*", tg_md.code(SUB_URL or "—")]
    if SUB_B64_URL:
        lines += ["", "🔗 *نسخه‌ی Base64* (کلاینت‌های قدیمی‌تر):", tg_md.code(SUB_B64_URL)]
    if SUB_IRAN_URL and iran_count:
        lines += ["", "🇮🇷 *فقط تأییدشده‌های ایران:*", tg_md.code(SUB_IRAN_URL)]
    if SUB_MIRROR_URL:
        lines += ["", "🪞 *آینه* (اگر لینک اصلی باز نشد):", tg_md.code(SUB_MIRROR_URL)]
    lines += [
        "",
        "📱 راهنما: کلاینت → Subscription → Add → لینک را پیست کنید → Update",
        "",
        f"🕐 {utc_now()}",
        f"🔄 دسته‌ی بعدی: {PUBLISH_INTERVAL_MIN} دقیقه دیگر",
        "",
        "#vless #vpn #v2ray #subscription #فیلترشکن",
    ]
    return tg_md.truncate("\n".join(lines), 3800)


def build_intro(configs: List[str], total_pool: int, stats: Optional[Dict] = None) -> str:
    """پیام کوتاه سرِ دسته — تا کاربر بداند این ۱۰ تا از کجا آمده‌اند."""
    iran = sum(1 for c in configs if vless.is_iran_verified(c))
    countries = sorted({vless.get_country(c) for c in configs if vless.get_country(c)})
    lines = [
        "🛡️ *دسته‌ی جدید کانفیگ*",
        "─" * 26,
        f"🕐 {utc_now()}",
        f"📦 در این دسته: *{len(configs)}* کانفیگ تست‌شده",
        f"🗂 کل پول تأییدشده: *{total_pool}*",
    ]
    if iran:
        lines.append(f"🇮🇷 تأییدشده از داخل ایران: *{iran}* از {len(configs)}")
    if countries:
        flags = " ".join(renderer.flag(code) for code in countries[:10])
        lines.append(f"🌍 کشورها: {flags}")
    if HTTP_TEST_ROUNDS > 1:
        lines.append(f"✅ هر کانفیگ {HTTP_TEST_ROUNDS} دور پشت سر هم تست شده")
    lines += ["", "⬇️ کانفیگ‌ها در پیام‌های بعدی — آخرین پیام لینک اشتراک است"]
    return "\n".join(lines)


# ─── ارسال ────────────────────────────────────────────────

class Publisher:
    """ارسال‌کننده. یک نمونه را می‌توان چند دوره استفاده کرد (ربات همین کار
    را می‌کند) — connect فقط یک بار انجام می‌شود."""

    def __init__(self, bot: Optional[Bot] = None):
        self.bot: Optional[Bot] = bot

    async def connect(self) -> bool:
        if self.bot is not None:
            return True
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
            logger.error("❌ TELEGRAM_BOT_TOKEN یا TELEGRAM_CHANNEL_ID نیست")
            return False
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            me = await bot.get_me()
            self.bot = bot
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

    async def send_plain(self, text: str) -> bool:
        """ارسال بدون parse_mode — آخرین تلاش وقتی Markdown خطا می‌دهد."""
        if self.bot is None:
            return False
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=text,
                disable_web_page_preview=True,
            )
            return True
        except TelegramError:
            return False

    async def publish_batch(
        self,
        configs: List[str],
        stats: Optional[Dict] = None,
        with_intro: bool = PUBLISH_INTRO,
    ) -> Dict[str, object]:
        """یک دوره: ۱۰ پیام کانفیگ + پیام لینک اشتراک.

        پیام «سرِ دسته» پیش‌فرض خاموش است تا شمارش دقیقاً همان چیزی بماند که
        خواسته شده: ۱۰ کانفیگ و پیام یازدهم لینک اشتراک. با PUBLISH_INTRO=1
        روشن می‌شود.

        خروجی: خلاصه‌ی دوره (چند فرستاده شد، چه شناسه‌هایی) تا ربات بتواند
        در /status نشان دهد.
        """
        result: Dict[str, object] = {"selected": 0, "sent": 0, "failed": 0, "ids": []}
        if not configs:
            logger.warning("⚠️ کانفیگ برای ارسال نیست")
            return result
        if not await self.connect():
            return result

        state = load_state()
        chosen = select_for_publish(configs, state)
        if not chosen:
            logger.warning("⚠️ همه‌ی کانفیگ‌ها در cooldown بودند")
            return result

        iran_total = sum(1 for c in configs if vless.is_iran_verified(c))
        result["selected"] = len(chosen)
        result["ids"] = [vless.short_id(c) for c in chosen]

        if with_intro:
            await self.send(build_intro(chosen, len(configs), stats))
            await asyncio.sleep(PUBLISH_MSG_GAP_SEC)

        sent = 0
        failed = 0
        for index, cfg in enumerate(chosen, 1):
            card = renderer.spec_card(cfg, index, len(chosen), HTTP_TEST_ROUNDS)
            ok = await self.send(card)
            if not ok:
                # کارت رد شد (احتمالاً entity خراب در اسم)؛ حداقل خود لینک
                # باید برسد — کاربر برای همین آمده.
                ok = await self.send_plain(f"#{vless.short_id(cfg)}\n{cfg}")
            if ok:
                sent += 1
            else:
                failed += 1
                logger.error(f"❌ کانفیگ {index}/{len(chosen)} ارسال نشد")
            await asyncio.sleep(PUBLISH_MSG_GAP_SEC)

        if await self.send(build_sub_message(len(configs), iran_total)):
            sent += 1
        else:
            failed += 1

        save_state(mark_published(state, chosen))
        result["sent"] = sent
        result["failed"] = failed
        logger.info(
            f"📤 دوره‌ی انتشار: {len(chosen)} کانفیگ + لینک اشتراک | "
            f"{sent} پیام موفق، {failed} ناموفق"
        )
        return result

    async def publish(self, configs: List[str], stats: Optional[Dict] = None) -> bool:
        """سازگاری با main.py — یک دوره پس از پایان pipeline."""
        result = await self.publish_batch(configs, stats)
        return bool(result["sent"]) and not result["failed"]

    async def send_error(self, error: str) -> None:
        """گزارش خطا به کانال. اگه ربات وصل نیست اول وصل میشه."""
        try:
            if not await self.connect():
                return
            await self.send(
                f"⚠️ *خطا در آپدیت*\n"
                f"🕐 {utc_now('%H:%M UTC')}\n"
                f"❌ {tg_md.strip_md(error, 200)}"
            )
        except Exception:
            pass
