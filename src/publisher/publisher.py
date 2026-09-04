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
PUBLISH_STATE_FILE نگه می‌دارد هر کانفیگ در کدام دوره پست شده.

انتظار (cooldown) **خودتنظیم** است، نه ثابت: `effective_cooldown` آن را از
اندازه‌ی پول حساب می‌کند، پس پولِ ۷۲۳ تایی با ۱۰ پست در هر دوره حدود ۷۲ دوره
انتظار می‌گیرد و یک بار کامل چرخیده می‌شود. با ثابتِ ۶ دوره، بهترین کانفیگ
نیم‌ساعت بعد برمی‌گشت و چون سرِ صف بود همان لحظه انتخاب می‌شد — عملاً گردش
ابدی روی ~۶۰ کانفیگ اول. سطل‌های انتخاب هم مرتب شدند: **ندیده قبل از
دیده‌شده**، حتی اگر کیفیتش کمی پایین‌تر باشد.

اولویت انتخاب: اول کانفیگ‌های تأییدشده از ایران (برچسب IR در fragment)، بعد
بین‌المللی‌ها به ترتیب تأخیر تونل، و اگر سهمیه پر نشد از پول ذخیره‌ی
تست‌نشده. هیچ‌جا فیلتر «فقط ایران» نداریم: قبلاً دوره‌هایی با ۳ کانفیگ پست
می‌شد چون پول تأییدشده کوچک بود، و کاربر صریح گفت سهمیه‌ی ۱۰ نباید بشکند.
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
    HTTP_TEST_ROUNDS, PUBLISH_COOLDOWN, PUBLISH_COOLDOWN_AUTO,
    PUBLISH_COOLDOWN_MAX, PUBLISH_COUNT, PUBLISH_FILL_FROM_POOL,
    PUBLISH_INTERVAL_MIN, PUBLISH_INTRO, PUBLISH_MSG_GAP_SEC,
    PUBLISH_STATE_FILE, PUBLISH_STRICT_COUNT, SUB_B64_URL, SUB_INTL_URL,
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


def effective_cooldown(pool_size: int, count: int) -> int:
    """چند دوره یک کانفیگ کنار بماند — از *اندازه‌ی پول*، نه یک عدد ثابت.

    اشکالی که این تابع حل می‌کند، شکایت صریح کاربر بود: «کانفیگ‌هایی که قبلاً
    کار کرده‌اند نباید دوباره و دوباره فرستاده شوند.» با ثابتِ ۶ دوره و پول
    ۷۲۳ تایی، بهترین کانفیگ نیم‌ساعت بعد دوباره واجد شرط می‌شد و چون سرِ صف
    rank_key بود همان لحظه انتخاب می‌شد؛ نتیجه‌اش گردش ابدی روی ~۶۰ کانفیگ
    اول و ندیده ماندن ۶۶۰ تای دیگر بود.

    عدد درست از خود پول درمی‌آید: ‏۷۲۳ کانفیگ با ۱۰ پست در هر دوره یعنی
    ۷۲ دوره تا یک بار کامل چرخیدن. سقف MAX هست تا پولِ بزرگ باعث نشود
    کانفیگی عملاً هیچ‌وقت برنگردد و حافظه‌ی `posted` بی‌مرز رشد کند.
    """
    floor = max(1, PUBLISH_COOLDOWN)
    if not PUBLISH_COOLDOWN_AUTO or pool_size <= 0 or count <= 0:
        return floor
    return max(floor, min(PUBLISH_COOLDOWN_MAX, pool_size // count))


def select_for_publish(
    configs: List[str],
    state: Dict,
    count: int = 0,
    reserve: Optional[List[str]] = None,
) -> List[str]:
    """انتخاب کانفیگ‌های این دوره — سهمیه *باید* پر شود.

    چهار سطل، به همین ترتیب اولویت:
      ۱. **ندیده** — هیچ‌وقت پست نشده. بین خودشان با `rank_key` مرتب می‌شوند
         (تأییدشده‌ی ایران اول، بعد کم‌تأخیرترین) و پول تأییدشده جلوی ذخیره است.
      ۲. **سرد** — پست شده ولی دوره‌ی انتظارش تمام شده. قدیمی‌ترین اول.
      ۳. **گرم** — هنوز در دوره‌ی انتظار است؛ فقط اگر سهمیه جور دیگری پر نشود.

    قاعده‌ی مرکزی این تابع: **کانفیگِ ندیده همیشه از کانفیگِ دیده‌شده جلوتر
    است، حتی اگر کیفیتش کمی پایین‌تر باشد.** نسخه‌ی قبلی فقط `rank_key` را
    نگاه می‌کرد و سرِ صف را برمی‌داشت، پس تا وقتی بهترین کانفیگ در انتظار
    نبود همیشه همان انتخاب می‌شد و پول عملاً هیچ‌وقت پیمایش نمی‌شد. مستقیم
    همان چیزی که کاربر دید: «مثل اینکه دیگر دنبال کانفیگ نمی‌گردد و همان‌های
    قبلی را می‌فرستد.»

    چرا «تازه‌ی ذخیره» جلوتر از «کهنه‌ی تأییدشده» است: تکرار کانفیگی که چند
    دقیقه پیش پست شده برای کاربر چیز تازه‌ای ندارد، ولی کانفیگ ذخیره یک
    گزینه‌ی *ندیده* است — و برچسب کارتش صریح می‌گوید تونلش تست نشده.

    مشکلی که این تابع قبلاً حل کرد و باید حل‌شده بماند: در دوره‌های واقعی فقط
    ۳ کانفیگ پست می‌شد، چون پول تأییدشده کوچک بود و چیزی جایش را پر نمی‌کرد.
    """
    count = count or PUBLISH_COUNT
    if count <= 0:
        return []
    cycle = int(state.get("cycle", 0))
    posted: Dict[str, int] = state.get("posted", {})

    # tier صفر پول تأییدشده است، tier یک ذخیره. کلید یکتایی short_id است نه
    # رشته‌ی کامل: یک endpoint می‌تواند در دو پول با برچسب تأخیر متفاوت باشد.
    groups = (configs or [], reserve or [])
    unique: List[Tuple[int, str, str]] = []
    seen: set = set()
    for tier, group in enumerate(groups):
        for cfg in sorted(dict.fromkeys(group), key=rank_key):
            sid = vless.short_id(cfg)
            if sid in seen:
                continue
            seen.add(sid)
            unique.append((tier, sid, cfg))

    cooldown = effective_cooldown(len(unique), count)

    unseen: List[str] = []
    cooled: List[Tuple[int, int, float, str]] = []
    hot: List[Tuple[int, int, float, str]] = []
    for tier, sid, cfg in unique:
        last = posted.get(sid)
        if last is None:
            unseen.append(cfg)
            continue
        age_key = (int(last), tier, vless.get_latency_ms(cfg), cfg)
        (cooled if cycle - int(last) >= cooldown else hot).append(age_key)

    chosen = unseen[:count]
    for bucket in (cooled, hot):
        if len(chosen) >= count:
            break
        bucket.sort(key=lambda item: item[:3])
        chosen.extend(cfg for *_, cfg in bucket[: count - len(chosen)])
    return chosen[:count]


def reserve_ids(configs: List[str], reserve: Optional[List[str]]) -> set:
    """شناسه‌هایی که *فقط* در پول ذخیره‌اند — برای برچسب «تست‌نشده».

    اگر یک endpoint در هر دو پول باشد، تأییدشده حساب می‌شود: نسخه‌ی تأییدشده
    ادعای قوی‌تری دارد و همان است که انتخاب شده.
    """
    if not reserve:
        return set()
    verified = {vless.short_id(c) for c in configs or []}
    return {vless.short_id(c) for c in reserve} - verified


def mark_published(state: Dict, configs: List[str], cooldown: int = 0) -> Dict:
    """ثبت اینکه این کانفیگ‌ها در دوره‌ی جاری پست شدند.

    cooldown انتظارِ *مؤثرِ* همین دوره است (از `effective_cooldown`). مرز هرس
    از آن حساب می‌شود نه از ثابتِ PUBLISH_COOLDOWN: با انتظارِ خودتنظیم،
    هرسِ زودتر از موعد یعنی کانفیگِ پست‌شده دوباره «ندیده» به نظر بیاید و
    همان تکرارِ قبلی برگردد — دقیقاً چیزی که می‌خواستیم از بین ببریم.
    """
    cycle = int(state.get("cycle", 0)) + 1
    posted: Dict[str, int] = dict(state.get("posted", {}))
    for cfg in configs:
        posted[vless.short_id(cfg)] = cycle
    # هرس: شناسه‌هایی که خیلی قدیمی‌اند دیگر روی تصمیم اثر ندارند و فایل را
    # بی‌دلیل بزرگ می‌کنند. ضریب ۲ حاشیه می‌دهد تا کوچک شدن ناگهانی پول
    # (یک اجرای ناموفق pipeline) حافظه‌ی چرخش را دور نریزد.
    horizon = cycle - max(1, cooldown or PUBLISH_COOLDOWN) * 2
    posted = {k: v for k, v in posted.items() if v >= horizon}
    return {
        "cycle": cycle,
        "posted": posted,
        "last_publish": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_count": len(configs),
    }


# ─── متن پیام‌ها ───────────────────────────────────────────

def build_sub_message(total: int, iran_count: int = 0, intl_count: int = 0) -> str:
    """پیام یازدهم: لینک subscription — همان چیزی که کاربر خواست زیر آخرین پست.

    چند لینک می‌دهیم چون کلاینت‌ها یکسان نیستند: v2rayNG/NekoBox نسخه‌های
    قدیمی فهرست متنی را نمی‌خوانند و فقط Base64 را قبول می‌کنند.

    تفکیک داخلی/خارجی در انتهای همین مسیر انجام می‌شود: یک لینک برای کسی که
    از ایران وصل می‌شود (فقط endpoint هایی که نود ایرانی دیده جواب می‌دهند) و
    یک لینک برای بقیه. قاطی کردنشان یعنی کاربر ایرانی نصف فهرست را بی‌فایده
    امتحان می‌کند.
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
    if intl_count:
        lines.append(f"🌍 بین‌المللی: *{intl_count}*")
    lines += ["", "🔗 *لینک اصلی* (همه):", tg_md.code(SUB_URL or "—")]
    if SUB_B64_URL:
        lines += ["", "🔗 *نسخه‌ی Base64* (کلاینت‌های قدیمی‌تر):", tg_md.code(SUB_B64_URL)]
    if SUB_IRAN_URL and iran_count:
        lines += [
            "",
            "🇮🇷 *ویژه‌ی داخل ایران* (تأییدشده از نود ایرانی):",
            tg_md.code(SUB_IRAN_URL),
        ]
    if SUB_INTL_URL and intl_count:
        lines += [
            "",
            "🌍 *ویژه‌ی خارج / ISP دیگر:*",
            tg_md.code(SUB_INTL_URL),
        ]
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

    async def send_card(self, config: str, index: int, total: int, badge: str = "") -> bool:
        """یک کارت کانفیگ. اگر Markdown رد شد، خودِ لینک خام فرستاده می‌شود.

        چرا fallback: اسم کانفیگ از منابع عمومی می‌آید و یک `_` تنها باعث
        خطای ۴۰۰ تلگرام می‌شود؛ کاربر برای *لینک* آمده، نه برای کارت.
        """
        rounds = 0 if badge else HTTP_TEST_ROUNDS
        ok = await self.send(renderer.spec_card(config, index, total, rounds, badge))
        if not ok:
            ok = await self.send_plain(f"#{vless.short_id(config)}\n{config}")
        return ok

    async def publish_batch(
        self,
        configs: List[str],
        stats: Optional[Dict] = None,
        with_intro: bool = PUBLISH_INTRO,
        reserve: Optional[List[str]] = None,
        donated: Optional[List[str]] = None,
    ) -> Dict[str, object]:
        """یک دوره: ۱۰ پیام کانفیگ + اهدایی‌ها + پیام لینک اشتراک.

        پیام «سرِ دسته» پیش‌فرض خاموش است تا شمارش دقیقاً همان چیزی بماند که
        خواسته شده: ۱۰ کانفیگ و پیام آخر لینک اشتراک. با PUBLISH_INTRO=1
        روشن می‌شود.

        `reserve` پول ذخیره است (تست‌نشده‌ها) و فقط سهمیه‌ی خالی را پر می‌کند.
        `donated` کانفیگ‌های اهدایی‌اند و *اضافه بر* سهمیه‌ی ۱۰تایی پست
        می‌شوند. صداکننده باید `donated_sent` خروجی را sent علامت بزند —
        این‌جا علامت نمی‌زنیم تا publisher به ماژول صف وابسته نشود.

        خروجی: خلاصه‌ی دوره تا ربات در /status نشان دهد.
        """
        result: Dict[str, object] = {
            "selected": 0, "sent": 0, "failed": 0, "ids": [],
            "from_pool": 0, "quota_short": 0, "donated_sent": [],
        }
        pool = list(reserve or []) if PUBLISH_FILL_FROM_POOL else []
        donated = [c for c in (donated or []) if isinstance(c, str) and c.strip()]
        if not configs and not pool and not donated:
            logger.warning("⚠️ کانفیگ برای ارسال نیست")
            return result
        if not await self.connect():
            return result

        state = load_state()
        chosen = select_for_publish(configs, state, reserve=pool)
        # همان انتظاری که انتخاب با آن تصمیم گرفت، تا هرسِ حافظه با آن جور باشد.
        unique_pool = len({vless.short_id(c) for c in list(configs) + pool})
        cooldown = effective_cooldown(unique_pool, PUBLISH_COUNT)
        if not chosen and not donated:
            logger.warning("⚠️ چیزی برای ارسال نماند")
            return result

        pool_only = reserve_ids(configs, pool)
        from_pool = sum(1 for c in chosen if vless.short_id(c) in pool_only)
        shortfall = max(0, PUBLISH_COUNT - len(chosen))
        if shortfall and PUBLISH_STRICT_COUNT:
            logger.warning(
                f"⚠️ سهمیه‌ی {PUBLISH_COUNT}تایی پر نشد — فقط {len(chosen)} "
                f"کانفیگ یکتا در دسترس بود (تأییدشده {len(configs)}، "
                f"ذخیره {len(pool)})"
            )
        # چند تای این دسته را کاربر قبلاً دیده؟ عدد صفر یعنی چرخش سالم است.
        already = sum(
            1 for c in chosen if vless.short_id(c) in (state.get("posted") or {})
        )
        if already:
            logger.warning(
                f"♻️ {already} از {len(chosen)} کانفیگ این دوره تکراری‌اند — "
                "پول برای انتظارِ کامل کوچک است"
            )
        iran_total = sum(1 for c in configs if vless.is_iran_verified(c))
        intl_total = max(0, len(set(configs)) - iran_total)
        result.update({
            "selected": len(chosen),
            "ids": [vless.short_id(c) for c in chosen],
            "from_pool": from_pool,
            "quota_short": shortfall,
            "repeated": already,
            "cooldown": cooldown,
        })

        if with_intro:
            await self.send(build_intro(chosen, len(configs), stats))
            await asyncio.sleep(PUBLISH_MSG_GAP_SEC)

        total_cards = len(chosen) + len(donated)
        sent = 0
        failed = 0
        index = 0
        donated_ok: List[str] = []
        for cfg in chosen:
            index += 1
            badge = "pool" if vless.short_id(cfg) in pool_only else ""
            if await self.send_card(cfg, index, total_cards, badge):
                sent += 1
            else:
                failed += 1
                logger.error(f"❌ کانفیگ {index}/{total_cards} ارسال نشد")
            await asyncio.sleep(PUBLISH_MSG_GAP_SEC)

        # اهدایی‌ها آخر می‌آیند و *اضافه بر* سهمیه‌اند. فقط موفق‌ها برگردانده
        # می‌شوند: کانفیگی که ارسالش شکست خورد در وضعیت taken می‌ماند و
        # هیچ‌وقت خودکار دوباره پست نمی‌شود — قرارداد «حداکثر یک بار».
        for cfg in donated:
            index += 1
            if await self.send_card(cfg, index, total_cards, "donated"):
                sent += 1
                donated_ok.append(cfg)
            else:
                failed += 1
                logger.error(f"❌ اهدایی {index}/{total_cards} ارسال نشد")
            await asyncio.sleep(PUBLISH_MSG_GAP_SEC)

        if await self.send(build_sub_message(len(configs), iran_total, intl_total)):
            sent += 1
        else:
            failed += 1

        save_state(mark_published(state, chosen + donated_ok, cooldown))
        result["sent"] = sent
        result["failed"] = failed
        result["donated_sent"] = donated_ok
        logger.info(
            f"📤 دوره‌ی انتشار: {len(chosen)}/{PUBLISH_COUNT} کانفیگ"
            + (f" ({from_pool} از ذخیره)" if from_pool else "")
            + (f" + {len(donated_ok)} اهدایی" if donated_ok else "")
            + f" + لینک اشتراک | {sent} پیام موفق، {failed} ناموفق"
        )
        return result

    async def publish(
        self,
        configs: List[str],
        stats: Optional[Dict] = None,
        reserve: Optional[List[str]] = None,
        donated: Optional[List[str]] = None,
    ) -> bool:
        """سازگاری با main.py — یک دوره پس از پایان pipeline."""
        result = await self.publish_batch(
            configs, stats, reserve=reserve, donated=donated,
        )
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
