"""
VPN Config Bot v2 — ربات تلگرام + انتشارگر کانال.

دو نقش در یک پروسه
──────────────────
۱. پاسخ به کاربران: دستورها، دکمه‌های ثابتِ پایین صفحه، QR.
۲. انتشار خودکار در کانال هر PUBLISH_INTERVAL_MIN دقیقه: ۱۰ کانفیگ در ۱۰
   پیام مستقل + پیام یازدهم با لینک اشتراک.

چرا انتشار اینجاست و نه در cron گیت‌هاب: زمان‌بندی cron گیت‌هاب best-effort
است (تأخیر چند ده دقیقه‌ای عادی است) و کمترین فاصله‌ی عملی‌اش ~۱۵ دقیقه، در
حالی که این پروسه روی Railway همیشه روشن است. APScheduler نصب نیست و
JobQueue هم به آن نیاز دارد، پس حلقه‌ی انتشار asyncio خام است.

تازه‌های این نسخه
─────────────────
  • دکمه‌های پایین چت (ReplyKeyboardMarkup) — کاربر برای کارهای رایج چیزی
    تایپ نمی‌کند. پنهان/نمایش‌شان کارِ آیکنِ خودِ تلگرام کنار کادر نوشتن است،
    نه دستور و دکمه‌ی خودساخته (توضیح کامل: بخش «دکمه‌های پایین صفحه»).
  • کارت مشخصات از src/publisher/renderer.py می‌آید؛ دقیقاً همان کارتی که در
    کانال پست می‌شود، پس ربات و کانال از هم واگرا نمی‌شوند.
  • /iran فقط کانفیگ‌های تأییدشده از داخل ایران (برچسب IR در fragment).
  • cache از iran.txt و index.json هم پر می‌شود، نه فقط valid.txt.
  • /publish برای ادمین: یک دوره‌ی انتشار دستی، بدون انتظار تا تیک بعدی.
  • کلیدهای آمار به لایه‌های ۱..۷ آپدیت شدند (لایه‌ی دسترسی از ایران اضافه شد).

اصلاحات نسخه‌های قبل که حفظ شده‌اند:
  • هر مقدار پویا از src.tg_md رد می‌شود (وگرنه خطای «Can't parse entities»).
  • refresh_cache در همه‌ی دستورها و callback ها اجرا می‌شود.
  • _last_fetch فقط در صورت موفقیت جلو می‌رود.
  • /add در configs/manual.txt نوشته می‌شود، نه valid.txt.
"""
import asyncio
import functools
import io
import json
import os
import random
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

import aiohttp
import qrcode
from telegram import (
    BotCommand, BotCommandScopeChat, InlineKeyboardMarkup, ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src import donations, tg_md, tg_ui, vless
from src.clean_ip import REVIVE_MARK
from src.config import (
    ADMIN_IDS, AUTO_PUBLISH, BOT_STATE_FILE, DONATE_ENABLED,
    DONATE_MAX_PER_DAY, DONATE_MAX_PER_MSG, GITHUB_REPO, GITHUB_TOKEN,
    HEALTH_FILE, HTTP_TEST_ROUNDS, INDEX_FILE, INTL_FILE, IRAN_FILE,
    MANUAL_FILE, POOL_FILE, PUBLISH_COUNT, PUBLISH_DONATED_COUNT,
    PUBLISH_FILL_FROM_POOL, PUBLISH_INTERVAL_MIN, RAW_BASE, STATS_FILE,
    SUB_B64_URL, SUB_INTL_URL, SUB_IRAN_URL, SUB_URL, TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHANNEL_ID, TG_SAFE_MSG_LEN, VALID_FILE, country_sub_url,
)
from src.logger import get_logger
from src.publisher import renderer
from src.publisher.publisher import Publisher

logger = get_logger("bot")

# ─── وضعیت ماندگار ربات ───────────────────────────────────
# قبلاً /off فقط یک متغیر در حافظه بود: با هر restart (روی Railway مثلاً بعد
# از deploy) ربات خودش روشن می‌شد و ادمین خبر نداشت. الان روی دیسک می‌نشیند.
#
# BOT_ENABLED  — پاسخ به کاربران عادی. ادمین همیشه دسترسی دارد تا بتواند
#                دوباره روشنش کند؛ وگرنه با یک /off ربات قابل بازیابی نبود.
# PUBLISH_PAUSED — فقط حلقه‌ی انتشار خودکار. /publish دستی کار می‌کند.

BOT_ENABLED = True
PUBLISH_PAUSED = False


def load_bot_state() -> None:
    """خواندن وضعیت از دیسک. فایل نبود = پیش‌فرض (روشن)."""
    global BOT_ENABLED, PUBLISH_PAUSED
    try:
        with open(BOT_STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    BOT_ENABLED = bool(data.get("enabled", True))
    PUBLISH_PAUSED = bool(data.get("publish_paused", False))
    logger.info(
        f"⚙️ وضعیت ذخیره‌شده: ربات {'روشن' if BOT_ENABLED else 'خاموش'} | "
        f"انتشار {'مکث' if PUBLISH_PAUSED else 'جاری'}"
    )


def save_bot_state() -> bool:
    """ذخیره‌ی وضعیت. شکست خوردنش ربات را نمی‌خواباند، فقط ماندگار نیست."""
    try:
        parent = os.path.dirname(BOT_STATE_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = f"{BOT_STATE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(
                {"enabled": BOT_ENABLED, "publish_paused": PUBLISH_PAUSED},
                fh, ensure_ascii=False, indent=2,
            )
        os.replace(tmp, BOT_STATE_FILE)
        return True
    except OSError as exc:
        logger.warning(f"⚠️ ذخیره‌ی وضعیت ربات ناموفق: {exc}")
        return False


# آدرس فایل‌ها برای *خواندن*؛ SUB_* برای نمایش به کاربر است و ممکن است
# بعداً به دامنه‌ی دیگری اشاره کند.
VALID_URL = f"{RAW_BASE}/{VALID_FILE}" if RAW_BASE else ""
IRAN_URL = f"{RAW_BASE}/{IRAN_FILE}" if RAW_BASE else ""
STATS_URL = f"{RAW_BASE}/{STATS_FILE}" if RAW_BASE else ""
INDEX_URL = f"{RAW_BASE}/{INDEX_FILE}" if RAW_BASE else ""
# پول ذخیره: کانفیگ‌های *تست‌نشده* (از سهم MAX_HTTP_TEST بیرون ماندند).
# «تست نشد» مساوی «رد شد» نیست؛ این‌ها فقط برای پر کردن سهمیه‌ی خالی‌اند.
POOL_URL = f"{RAW_BASE}/{POOL_FILE}" if RAW_BASE else ""

CACHE_TTL = 300          # ۵ دقیقه — هم‌اندازه‌ی cache خود raw.githubusercontent
LIST_LIMIT = 10

_cache: Dict[str, object] = {
    "configs": [], "iran": [], "pool": [], "stats": {}, "index": {},
}
_last_fetch: float = 0.0
# خلاصه‌ی آخرین دوره‌های انتشار — /status ادمین این را نشان می‌دهد.
_publish_log: List[Dict] = []


def _lines(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


async def fetch_from_github(url: str) -> str:
    if not url:
        return ""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.text(encoding="utf-8")
                logger.debug(f"GitHub HTTP {resp.status}: {url}")
    except Exception as exc:
        logger.debug(f"خطا دریافت از GitHub: {exc}")
    return ""


async def refresh_cache(force: bool = False) -> None:
    """آپدیت cache از GitHub — پنج فایل با هم.

    _last_fetch فقط در صورت موفقیت جلو می‌رود تا یک قطعی موقت شبکه، cache
    خالی را برای ۵ دقیقه قفل نکند.
    """
    global _last_fetch

    if not force and _cache["configs"] and time.time() - _last_fetch < CACHE_TTL:
        return

    valid_text, iran_text, pool_text, stats_text, index_text = await asyncio.gather(
        fetch_from_github(VALID_URL),
        fetch_from_github(IRAN_URL),
        fetch_from_github(POOL_URL),
        fetch_from_github(STATS_URL),
        fetch_from_github(INDEX_URL),
    )

    ok = False
    if valid_text:
        _cache["configs"] = _lines(valid_text)
        ok = True
    if iran_text:
        _cache["iran"] = _lines(iran_text)
    if pool_text:
        _cache["pool"] = _lines(pool_text)
    for key, text in (("stats", stats_text), ("index", index_text)):
        if not text:
            continue
        try:
            _cache[key] = json.loads(text)
            ok = True
        except ValueError:
            logger.debug(f"{key} خراب بود")

    if ok:
        _last_fetch = time.time()
        logger.info(
            f"🔄 cache: {len(_cache['configs'])} کانفیگ | "
            f"{len(_cache['iran'])} تأییدشده‌ی ایران | "
            f"{len(_cache['pool'])} ذخیره"
        )


# ─── منبع داده: تازه‌ترین برنده است ────────────────────────

def _local_timestamp() -> str:
    """timestamp فایل آمار محلی؛ خالی یعنی نامعلوم."""
    try:
        with open(STATS_FILE, encoding="utf-8") as fh:
            return str(json.load(fh).get("timestamp", ""))
    except (OSError, ValueError):
        return ""


def _read_local(path: str) -> List[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return _lines(fh.read())
    except OSError:
        return []


def load_configs() -> List[str]:
    """تازه‌ترین منبع برنده است، نه فایل محلی.

    روی Railway فایل configs/valid.txt همان snapshot لحظه‌ی deploy است و
    ساعت‌ها یا روزها کهنه می‌شود، ولی چون خالی نیست قبلاً همیشه برنده می‌شد
    و refresh_cache عملاً بی‌مصرف بود؛ نتیجه‌اش این بود که ربات کانفیگ مرده
    تحویل می‌داد در حالی که لینک subscription تازه بود. timestamp ها
    ISO-8601 با همان offset هستند، پس مقایسه‌ی رشته‌ای درست است.
    """
    remote = list(_cache["configs"])  # type: ignore[arg-type]
    if remote and str(_cache["stats"].get("timestamp", "")) > _local_timestamp():  # type: ignore[union-attr]
        return remote
    return _read_local(VALID_FILE) or remote


def load_iran_configs() -> List[str]:
    """کانفیگ‌های تأییدشده از داخل ایران.

    iran.txt منبع اصلی است، ولی اگر اجرای قبلی آن را نساخته باشد (یا مخزن
    قدیمی باشد) از برچسب IR داخل خود لینک‌ها بازسازی می‌شود — برچسب همراه
    لینک سفر می‌کند، پس هیچ‌وقت به فایل دوم وابسته نیستیم.
    """
    remote = list(_cache["iran"])  # type: ignore[arg-type]
    if remote:
        return remote
    local = _read_local(IRAN_FILE)
    if local:
        return local
    return [c for c in load_configs() if vless.is_iran_verified(c)]


def load_pool_configs() -> List[str]:
    """پول ذخیره — کانفیگ‌های *تست‌نشده*، نه ردشده.

    این‌ها لایه‌های فرمت/TCP/دسترسی/TLS را پاس کرده‌اند ولی سهم
    MAX_HTTP_TEST پر شده بود و تونلشان با xray امتحان نشد. فقط برای پر کردن
    سهمیه‌ی خالیِ دوره استفاده می‌شوند و کارتشان صریح می‌گوید تست‌نشده‌اند.
    """
    remote = list(_cache["pool"])  # type: ignore[arg-type]
    return remote or _read_local(POOL_FILE)


def load_publish_pool() -> List[str]:
    """پول تأییدشده‌ی دوره‌ی انتشار — همه‌ی منابع، بدون فیلتر کشور.

    مشکلی که این تابع حل می‌کند: دوره‌هایی با ۳ کانفیگ پست می‌شد چون عملاً
    فقط کانفیگ‌های تأییدشده‌ی ایران به انتشار می‌رسید. سهمیه‌ی ۱۰تایی نباید
    بشکند، پس داخلی و بین‌المللی و دستی همه در یک پول می‌روند و
    `publisher.rank_key` فقط *ترتیب* را تعیین می‌کند: ایران اول، بعد بقیه.

    ترتیب اضافه‌شدن مهم است: valid.txt پایه است و iran.txt/international.txt
    فقط چیزهایی را اضافه می‌کنند که در آن نیستند (اگر اجرای آخر ناقص مانده
    باشد). manual.txt آخر می‌آید چون ادمین آگاهانه اضافه‌اش کرده.
    """
    pool: List[str] = []
    seen: set = set()
    for group in (
        load_configs(),
        load_iran_configs(),
        _read_local(INTL_FILE),
        _read_local(MANUAL_FILE),
    ):
        for cfg in group:
            sid = vless.short_id(cfg)
            if sid in seen:
                continue
            seen.add(sid)
            pool.append(cfg)
    return pool


def load_stats() -> dict:
    """همان منطق load_configs: هرکدام تازه‌تر است."""
    local: dict = {}
    try:
        with open(STATS_FILE, encoding="utf-8") as fh:
            local = json.load(fh)
    except (OSError, ValueError):
        pass

    remote: dict = _cache["stats"]  # type: ignore[assignment]
    if remote and str(remote.get("timestamp", "")) > str(local.get("timestamp", "")):
        return dict(remote)
    return local or dict(remote)


# ─── کمکی ─────────────────────────────────────────────────

def is_admin(uid: Optional[int]) -> bool:
    return uid is not None and uid in ADMIN_IDS


def sort_best(configs: List[str]) -> List[str]:
    """تأییدشده‌های ایران اول، بعد کم‌تأخیرترین — همان ترتیب کانال.

    rank_key در publisher همین است؛ اگر ربات ترتیب دیگری می‌داد، «بهترین
    کانفیگ» در ربات و در کانال دو چیز مختلف می‌شد.
    """
    return sorted(
        configs,
        key=lambda c: (
            0 if vless.is_iran_verified(c) else 1,
            vless.get_latency_ms(c),
            0 if vless.is_reality(c) else 1,
        ),
    )


def make_qr(text: str) -> io.BytesIO:
    qr = qrcode.QRCode(box_size=8, border=3)
    qr.add_data(text)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    buf.seek(0)
    return buf


# ─── دکمه‌های ثابت پایین صفحه ──────────────────────────────
# چرا این بخش عوض شد: کاربر گفت «آن چیزی نیست که از Bot Keyboard Toggle
# منظورم بود؛ یک آیکن شبکه/کیبورد کنار کادر نوشتن است، فیچر خودِ تلگرام.»
# درست هم بود — و علتش یک پارامتر بود. مستند Bot API برای is_persistent:
# «Requests clients to always show the keyboard when the regular keyboard is
# hidden. Defaults to False, in which case the custom keyboard can be hidden
# and opened with a keyboard icon.» یعنی is_persistent=True *دقیقاً همان
# آیکن را حذف می‌کند*. نسخه‌ی قبلی True می‌فرستاد و بعد با یک دستور و یک
# دکمه‌ی سرخِ خودساخته جایش را پر می‌کرد؛ حالا False است و کنترل، همان
# آیکنِ بومیِ کنار کادر نوشتن است — بدون دستور، بدون دکمه، در همه‌ی
# کلاینت‌ها یکسان.
#
# رنگ‌ها (Bot API 9.4) از src/tg_ui.py می‌آیند و روی PTB قدیمی‌تر خودکار حذف
# می‌شوند. رنگ هیچ‌وقت تنها حامل معنا نیست — متن هر دکمه خودش گویا است، وگرنه
# روی کلاینت قدیمی یا برای کاربر کم‌بینا دکمه‌ها از هم قابل تشخیص نبودند.

BTN_BEST = "⭐ بهترین"
BTN_IRAN = "🇮🇷 مخصوص ایران"
BTN_COUNTRY = "🌍 انتخاب کشور"
BTN_RANDOM = "🎲 رندوم"
BTN_LIST = "📋 لیست"
BTN_SUB = "🔗 اشتراک"
BTN_DONATE = "🎁 اهدای کانفیگ"
BTN_STATS = "📊 آمار"
BTN_QR = "📷 QR"
BTN_HELP = "❓ راهنما"
# ردیف ادمین — فقط برای کسی که در ADMIN_IDS است ساخته می‌شود. اینها همان
# دستورهای ادمین‌اند، پس اگر کاربر عادی متنشان را دستی تایپ کند، دکوراتور
# admin_only جلویش را می‌گیرد؛ کیبورد لایه‌ی راحتی است نه لایه‌ی امنیت.
BTN_A_PUBLISH = "📤 انتشار دستی"
BTN_A_STATUS = "🖥️ وضعیت"
BTN_A_DONATIONS = "🎁 صف اهدا"
BTN_A_TOGGLE = "⏸️ مکث انتشار"
BTN_A_QUALITY = "📶 کیفیت پول"
BTN_A_HEALTH = "🩺 سلامت منابع"

# هر دکمه رنگ دارد (خواسته‌ی کاربر) ولی رنگ‌ها بی‌قاعده نیستند، وگرنه وقتی
# همه‌چیز برجسته باشد هیچ‌چیز برجسته نیست:
#   سبز  = چیزی به تو *می‌دهد* (کانفیگ، اشتراک، اهدا)
#   آبی  = گشتن و دیدن (کشور، لیست، آمار، QR، راهنما)
#   سرخ  = چیزی را *خاموش* می‌کند (مکث انتشار)
_USER_ROWS = [
    [tg_ui.kb(BTN_BEST, tg_ui.SUCCESS), tg_ui.kb(BTN_IRAN, tg_ui.SUCCESS)],
    [tg_ui.kb(BTN_COUNTRY, tg_ui.PRIMARY), tg_ui.kb(BTN_RANDOM, tg_ui.SUCCESS)],
    [tg_ui.kb(BTN_LIST, tg_ui.PRIMARY), tg_ui.kb(BTN_SUB, tg_ui.SUCCESS)],
    [tg_ui.kb(BTN_DONATE, tg_ui.SUCCESS), tg_ui.kb(BTN_STATS, tg_ui.PRIMARY)],
    [tg_ui.kb(BTN_QR, tg_ui.PRIMARY), tg_ui.kb(BTN_HELP, tg_ui.PRIMARY)],
]

_ADMIN_ROWS = [
    [tg_ui.kb(BTN_A_PUBLISH, tg_ui.DANGER), tg_ui.kb(BTN_A_STATUS, tg_ui.PRIMARY)],
    [tg_ui.kb(BTN_A_DONATIONS, tg_ui.PRIMARY),
     tg_ui.kb(BTN_A_TOGGLE, tg_ui.DANGER)],
    [tg_ui.kb(BTN_A_QUALITY, tg_ui.PRIMARY),
     tg_ui.kb(BTN_A_HEALTH, tg_ui.PRIMARY)],
]


def _keyboard(rows, placeholder: str) -> ReplyKeyboardMarkup:
    """صفحه‌کلید ثابت. is_persistent عمداً پاس نمی‌شود (پیش‌فرض False).

    با False، تلگرام خودش آیکنِ کیبورد را کنار کادر نوشتن می‌گذارد تا کاربر
    دکمه‌ها را ببندد و باز کند. با True آن آیکن نیست و بستنِ دکمه‌ها فقط با
    ReplyKeyboardRemove از سمت ربات ممکن است — همان چیزی که کاربر نمی‌خواست.
    """
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        input_field_placeholder=placeholder,
    )


MAIN_KEYBOARD = _keyboard(_USER_ROWS, "یک دکمه را بزن یا /help")
# خواسته‌ی کاربر: «دکمه‌های مخصوص کاربر نباید برای ادمین دیده شوند.» پس
# کیبورد ادمین فقط ردیف‌های مدیریتی است، نه جمعِ دو تا. ادمین چیزی از دست
# نمی‌دهد: همه‌ی دستورهای کاربر در منوی دستورهای خودش ثبت می‌شوند
# (BotCommandScopeChat در setup_commands) و تایپ کردنشان هم کار می‌کند.
ADMIN_KEYBOARD = _keyboard(_ADMIN_ROWS, "دستور ادمین یا /help")


def keyboard_for(uid: Optional[int]) -> ReplyKeyboardMarkup:
    """کیبورد بر اساس نقش — ادمین کیبورد مدیریتی می‌بیند، نه کیبورد کاربر."""
    return ADMIN_KEYBOARD if is_admin(uid) else MAIN_KEYBOARD


async def reply(update: Update, text: str, **kwargs) -> None:
    """پاسخ امن: پیام ممکن است edit شده یا از callback آمده باشد."""
    message = update.effective_message
    if message is None:
        return
    try:
        await message.reply_text(
            tg_md.truncate(text, TG_SAFE_MSG_LEN),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
            **kwargs,
        )
    except Exception as exc:
        # آخرین تلاش بدون Markdown: بهتر از پیام نرسیده.
        logger.warning(f"⚠️ ارسال با Markdown ناموفق: {exc}")
        try:
            await message.reply_text(
                tg_md.strip_md(text, TG_SAFE_MSG_LEN),
                disable_web_page_preview=True,
                **kwargs,
            )
        except Exception:
            pass


def user_command(func: Callable) -> Callable:
    """چک روشن بودن ربات + آپدیت cache برای همه‌ی دستورهای کاربر."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not BOT_ENABLED and not is_admin(user.id if user else None):
            await reply(update, "🔴 ربات موقتاً خاموشه.")
            return
        await refresh_cache()
        return await func(update, context)

    return wrapper


def admin_only(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user.id if user else None):
            await reply(update, "⛔ فقط ادمین.")
            return
        return await func(update, context)

    return wrapper


def format_config_list(
    configs: List[str], title: str, max_n: int = LIST_LIMIT
) -> str:
    if not configs:
        return f"❌ کانفیگ {title} موجود نیست."
    lines = [f"📋 *{title}* — {len(configs)} کانفیگ", ""]
    for index, cfg in enumerate(configs[:max_n], 1):
        lines.append(f"*{index}.* {renderer.one_line(cfg)}")
        lines.append(tg_md.code(cfg))
        lines.append("")
    if len(configs) > max_n:
        lines.append(
            f"_… و {len(configs) - max_n} کانفیگ دیگر — لینک اشتراک همه را دارد._"
        )
    return "\n".join(lines)


def card_keyboard(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            tg_ui.ikb("📷 QR", tg_ui.PRIMARY, callback_data=f"qr_{index}"),
            tg_ui.ikb("▶️ بعدی", tg_ui.PRIMARY,
                      callback_data=f"get_next_{index + 1}"),
        ],
        [tg_ui.ikb("🔗 لینک اشتراک", tg_ui.SUCCESS, callback_data="sub")],
    ])


def card(config: str, index: int, total: int) -> str:
    """کارت مشخصات — همان تابعی که کانال استفاده می‌کند."""
    return renderer.spec_card(config, index, total, HTTP_TEST_ROUNDS)


# ─── دستورات کاربر ────────────────────────────────────────

@user_command
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    configs = load_configs()
    iran = load_iran_configs()
    stats = load_stats()
    ts = str(stats.get("timestamp", ""))[:16].replace("T", " ")
    user = update.effective_user
    name = tg_md.strip_md(user.first_name if user else "", 30) or "دوست من"

    text = (
        f"👋 سلام *{name}*!\n\n"
        f"🛡️ *VPN Config Bot*\n"
        f"کانفیگ‌های VLESS که ۷ لایه تست شده‌اند.\n\n"
        f"📊 *وضعیت:*\n"
        f"  ✅ {len(configs)} کانفیگ معتبر\n"
        f"  🇮🇷 {len(iran)} تأییدشده از داخل ایران\n"
        f"  🕐 آخرین آپدیت: {ts or 'نامشخص'}\n\n"
        f"👇 از دکمه‌های پایین استفاده کن — /help برای همه‌ی دستورها.\n"
        f"⌨️ دکمه‌ها اذیت می‌کنند؟ با آیکنِ کیبورد کنار کادر نوشتن ببندشان."
    )
    if is_admin(user.id if user else None):
        text += "\n\n🛠️ *دسترسی ادمین فعال* — کیبورد پایین، دستورهای مدیریتی است."
    await reply(update, text, reply_markup=keyboard_for(user.id if user else None))


@user_command
async def cmd_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بهترین کانفیگ: تأییدشده‌ی ایران با کم‌ترین تأخیر تونل."""
    configs = sort_best(load_configs())
    if not configs:
        await reply(update, "❌ هنوز کانفیگی موجود نیست.")
        return
    # ایندکس همان ترتیبی است که دکمه‌ها استفاده می‌کنند (sort_best).
    await reply(update, card(configs[0], 1, len(configs)),
                reply_markup=card_keyboard(0))


@user_command
async def cmd_iran(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کانفیگ‌هایی که از نودهای ایرانی check-host هم جواب دادند.

    این مهم‌ترین فهرست ربات است: بقیه‌ی لایه‌ها روی رانر آمریکایی گیت‌هاب
    اجرا می‌شوند و «سالم بودن سرور» را می‌سنجند، نه «باز شدن از ایران».
    """
    configs = sort_best(load_iran_configs())
    if not configs:
        await reply(
            update,
            "⚠️ فعلاً کانفیگ تأییدشده از داخل ایران نداریم.\n"
            "لایه‌ی check-host در اجرای بعدی دوباره تست می‌کند — "
            "تا آن موقع /get را امتحان کن.",
        )
        return
    text = format_config_list(configs, "🇮🇷 تأییدشده از داخل ایران")
    if SUB_IRAN_URL:
        text += f"\n\n🔗 *لینک اشتراک فقط ایرانی‌ها:*\n{tg_md.code(SUB_IRAN_URL)}"
    await reply(update, text)


@user_command
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, format_config_list(sort_best(load_configs()), "همه‌ی کانفیگ‌ها"))


@user_command
async def cmd_reality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    configs = [c for c in sort_best(load_configs()) if vless.is_reality(c)]
    await reply(update, format_config_list(configs, "🔐 Reality"))


@user_command
async def cmd_tls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    configs = [
        c for c in sort_best(load_configs())
        if vless.get_security_label(c) == "TLS"
    ]
    await reply(update, format_config_list(configs, "🔒 TLS"))


@user_command
async def cmd_random(update: Update, context: ContextTypes.DEFAULT_TYPE):
    configs = sort_best(load_configs())
    if not configs:
        await reply(update, "❌ کانفیگی موجود نیست.")
        return
    index = random.randrange(len(configs))
    await reply(
        update,
        card(configs[index], index + 1, len(configs)),
        reply_markup=InlineKeyboardMarkup([[
            tg_ui.ikb("🎲 یکی دیگه", tg_ui.PRIMARY, callback_data="random"),
            tg_ui.ikb("📷 QR", tg_ui.PRIMARY, callback_data=f"qr_{index}"),
        ]]),
    )


@user_command
async def cmd_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    configs = sort_best(load_configs())
    if not configs:
        await reply(update, "❌ کانفیگی موجود نیست.")
        return

    index = 0
    if context.args:
        try:
            index = int(context.args[0]) - 1
        except ValueError:
            await reply(update, "❌ شماره‌ی کانفیگ باید عدد باشه. مثال: `/qr 3`")
            return
    index = max(0, min(index, len(configs) - 1))
    await send_qr(update, configs[index], f"کانفیگ #{index + 1}")


async def send_qr(update: Update, payload: str, caption: str) -> None:
    message = update.effective_message
    if message is None:
        return
    try:
        await message.reply_photo(
            photo=make_qr(payload),
            caption=f"📷 QR — {tg_md.strip_md(caption, 60)}",
        )
    except Exception as exc:
        logger.warning(f"ساخت/ارسال QR ناموفق: {exc}")
        await reply(update, "❌ ساخت QR ناموفق بود.")


@user_command
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تأخیر اندازه‌گیری‌شده در pipeline (تست زنده نیست)."""
    configs = sort_best(load_configs())
    if not configs:
        await reply(update, "❌ کانفیگی موجود نیست.")
        return
    lines = ["⚡ *تأخیر کانفیگ‌ها* _(اندازه‌گیری آخرین اجرا)_", ""]
    for index, cfg in enumerate(configs[:LIST_LIMIT], 1):
        lines.append(f"*{index}.* {renderer.one_line(cfg)}")
    lines += ["", "🇮🇷 = از نودهای ایرانی هم تست شده"]
    await reply(update, "\n".join(lines))


@user_command
async def cmd_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    configs = load_configs()
    iran = load_iran_configs()
    if not SUB_URL:
        await reply(update, "⚠️ لینک subscription تنظیم نشده (GITHUB_REPO خالیه).")
        return
    lines = [
        "🔗 *لینک اشتراک (Subscription)*",
        "همه‌ی کانفیگ‌ها یک‌جا و همیشه به‌روز.",
        "",
        f"📦 {len(configs)} کانفیگ در این لینک:",
        tg_md.code(SUB_URL),
    ]
    if SUB_B64_URL:
        lines += ["", "🔗 *نسخه‌ی Base64* (کلاینت‌های قدیمی‌تر):",
                  tg_md.code(SUB_B64_URL)]
    if SUB_IRAN_URL and iran:
        lines += ["", f"🇮🇷 *فقط {len(iran)} تأییدشده‌ی ایران:*",
                  tg_md.code(SUB_IRAN_URL)]
    lines += [
        "",
        "📱 *نحوه‌ی استفاده:*",
        "• *v2rayNG:* ➕ → Import from URL",
        "• *Hiddify / NekoBox:* پروفایل جدید → از لینک",
        "• *NekoRay:* Profiles → New → Subscription",
    ]
    await reply(
        update, "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            [[tg_ui.ikb("📷 QR لینک", tg_ui.PRIMARY, callback_data="qr_sub")]]
        ),
    )


@user_command
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = load_stats()
    configs = load_configs()
    pipe = stats.get("pipeline", {}) or {}

    def layer(name: str, key: str) -> str:
        value = pipe.get(name)
        if not isinstance(value, dict):
            return "—"
        if value.get("skipped"):
            return "رد شد"
        return str(value.get(key, "—"))

    def iran_cell() -> str:
        """لایه ۴ دو عدد دارد: «عبورکرده» endpoint های بی‌حکم (سهمیه، سقف) را
        هم شامل می‌شود، «تأییدشده» همان‌هایی است که از نود ایرانی جواب دادند."""
        passed = layer("layer4_iran", "passed")
        verified = layer("layer4_iran", "verified")
        if verified.isdigit() and verified != passed:
            return f"{passed} ({verified} تأییدشده)"
        return passed

    counts: Dict[str, int] = {}
    for cfg in configs:
        code = vless.get_country(cfg)
        if code:
            counts[code] = counts.get(code, 0) + 1
    country_text = " | ".join(
        f"{renderer.flag(code)} {code}:{n}"
        for code, n in sorted(counts.items(), key=lambda x: -x[1])[:6]
    )

    labels = [vless.get_security_label(c) for c in configs]
    funnel = (pipe.get("summary") or {}).get("funnel") or []
    rounds = layer("layer7_http", "rounds")

    text = (
        f"📊 *آمار کامل*\n"
        f"{'─' * 26}\n"
        f"🕐 آخرین آپدیت: "
        f"{tg_md.code(str(stats.get('timestamp', '—'))[:19].replace('T', ' '))}\n"
        f"⏱️ مدت اجرا: {tg_md.code(str(stats.get('duration_seconds', '—')) + 's')}\n\n"
        f"📥 جمع‌آوری خام: {tg_md.code(stats.get('raw_collected', 0))}\n"
        f"✅ معتبر نهایی: *{stats.get('valid_configs', len(configs))}*\n"
        f"🇮🇷 تأییدشده از ایران: *{stats.get('iran_verified', 0)}*\n\n"
        f"🔬 *Pipeline ۷ لایه:*\n"
        f"  ۱ فرمت: {layer('layer1_format', 'valid')}\n"
        f"  ۲ حذف تکراری: {layer('layer2_dedup', 'unique')}\n"
        f"  ۳ TCP (فیلتر سخت): {layer('layer3_tcp', 'connected')}\n"
        f"  ۴ دسترسی از ایران: {iran_cell()}\n"
        f"  ۵ TLS: {layer('layer5_tls', 'passed')}\n"
        f"  ۶ Geo: {layer('layer6_geo', 'passed')}\n"
        f"  ۷ HTTP واقعی ({rounds} دور): {layer('layer7_http', 'passed')}\n"
        + (f"  قیف: {tg_md.code(' → '.join(str(n) for n in funnel))}\n" if funnel else "")
        + f"\n📡 *امنیت:* 🔐 {labels.count('Reality')} | "
        f"🔒 {labels.count('TLS')} | ☁️ {labels.count('Other')}\n"
        f"🌍 *کشورها:* {country_text or 'نامشخص'}"
    )
    await reply(update, text)


# ─── انتخاب کشور ──────────────────────────────────────────
# درخواست کاربر: «امکان انتخاب کانفیگ بر اساس موقعیت/کشور». برچسب کشور از
# لایه‌ی ۶ (Geo) در خود fragment نشسته، پس این‌جا فقط گروه‌بندی است — هیچ
# درخواست شبکه‌ای لازم نیست.
#
# شکایت کاربر: «چرا انتخاب کشور فقط آمریکا است؟» دو علت داشت. علت اصلی در
# لایه ۶ بود (کوئری به‌ازای هر کانفیگ → سهمیه‌ی API می‌سوخت → ۷۲٪ کانفیگ‌ها
# «نامعلوم») که همان‌جا درست شد؛ اندازه‌گیریِ بعدش روی داده‌ی واقعی ۳۸ کشور
# داد. علت دوم همین‌جا بود: صفحه‌کلید فقط ۱۲ دکمه می‌ساخت و بقیه فقط با
# تایپ کردن `/country XX` قابل دسترسی بودند. حالا صفحه‌بندی می‌شود.

COUNTRY_PAGE_SIZE = 12      # ۴ ردیف × ۳ ستون در هر صفحه


def country_counts(configs: List[str]) -> Dict[str, int]:
    """کد کشور → تعداد، از پرتعدادترین. بی‌برچسب‌ها شمرده نمی‌شوند."""
    counts: Dict[str, int] = {}
    for cfg in configs:
        code = vless.get_country(cfg)
        if code and len(code) == 2 and code.isalpha() and code != "XX":
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def country_page(
    counts: Dict[str, int], page: int = 0
) -> Tuple[List[Tuple[str, int]], int, int]:
    """(کشورهای این صفحه، شماره‌ی صفحه‌ی نرمال‌شده، تعداد صفحه‌ها).

    صفحه چرخشی است: بعد از آخرین صفحه به اولی برمی‌گردد. برای کاربری که
    دکمه را پشت‌سرهم می‌زند این طبیعی‌تر از دکمه‌ی خاموش است.
    """
    items = list(counts.items())
    pages = max(1, -(-len(items) // COUNTRY_PAGE_SIZE))
    page = page % pages if pages else 0
    start = page * COUNTRY_PAGE_SIZE
    return items[start:start + COUNTRY_PAGE_SIZE], page, pages


def country_keyboard(counts: Dict[str, int], page: int = 0) -> InlineKeyboardMarkup:
    """سه دکمه در هر ردیف — بیشتر از این روی موبایل متن دکمه بریده می‌شود.

    سه کشور پرکانفیگ (در صفحه‌ی اول) سبزند و بقیه آبی: عدد کنار پرچم هم
    همان را می‌گوید، پس رنگ فقط تأکید است نه تنها حامل معنا (کلاینت قدیمی
    رنگ را نشان نمی‌دهد).
    """
    shown, page, pages = country_page(counts, page)
    buttons = [
        tg_ui.ikb(f"{renderer.flag(code)} {code} ({n})",
                  tg_ui.SUCCESS if page == 0 and index < 3 else tg_ui.PRIMARY,
                  callback_data=f"co_{code}")
        for index, (code, n) in enumerate(shown)
    ]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    if pages > 1:
        rows.append([
            tg_ui.ikb("◀️", tg_ui.PRIMARY, callback_data=f"cop_{page - 1}"),
            tg_ui.ikb(f"صفحه {page + 1}/{pages}", tg_ui.PRIMARY,
                      callback_data=f"cop_{page}"),
            tg_ui.ikb("▶️", tg_ui.PRIMARY, callback_data=f"cop_{page + 1}"),
        ])
    rows.append([tg_ui.ikb("🇮🇷 تأییدشده از ایران", tg_ui.SUCCESS,
                           callback_data="iran")])
    return InlineKeyboardMarkup(rows)


async def show_country_list(update: Update, page: int = 0) -> None:
    """فهرست کشورها با صفحه‌بندی — هم برای `/country` و هم دکمه‌های صفحه."""
    counts = country_counts(load_configs())
    if not counts:
        await reply(update, "⚠️ فعلاً کانفیگی با برچسب کشور نداریم.")
        return
    shown, page, pages = country_page(counts, page)
    lines = [
        "🌍 *انتخاب بر اساس کشور*",
        f"{len(counts)} کشور | {sum(counts.values())} کانفیگ برچسب‌دار",
        "",
        " | ".join(f"{renderer.flag(c)} {c} {n}" for c, n in shown),
        "",
        "👇 کشور را انتخاب کن — یا `/country DE` را بنویس.",
    ]
    if pages > 1:
        lines.append(f"_صفحه {page + 1} از {pages}._")
    await reply(
        update, "\n".join(lines), reply_markup=country_keyboard(counts, page)
    )


@user_command
async def cmd_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/country` فهرست کشورها، `/country DE` کانفیگ‌های آن کشور."""
    if context.args:
        await show_country(update, context.args[0])
        return
    await show_country_list(update)


async def show_country(update: Update, raw_code: str) -> None:
    """کانفیگ‌های یک کشور + لینک اشتراک همان کشور.

    کد از ورودی کاربر یا callback_data می‌آید، پس اعتبارسنجی می‌شود؛
    `config.country_sub_url` هم خودش دوباره بررسی می‌کند (مسیر `../`).
    """
    code = (raw_code or "").strip().upper()[:2]
    if len(code) != 2 or not code.isalpha():
        await reply(update, "❌ کد کشور دو حرف انگلیسی است. مثال: `/country DE`")
        return
    configs = [c for c in sort_best(load_configs()) if vless.get_country(c) == code]
    if not configs:
        counts = country_counts(load_configs())
        await reply(
            update,
            f"❌ برای {renderer.flag(code)} *{code}* کانفیگی نداریم.\n"
            + (" | ".join(f"{c} {n}" for c, n in list(counts.items())[:8])
               if counts else ""),
            reply_markup=country_keyboard(counts) if counts else None,
        )
        return
    text = format_config_list(configs, f"{renderer.flag(code)} {code}")
    sub = country_sub_url(code)
    if sub:
        text += f"\n\n🔗 *لینک اشتراک {code}:*\n{tg_md.code(sub)}"
    await reply(update, text, reply_markup=InlineKeyboardMarkup(
        [[tg_ui.ikb("🌍 کشور دیگر", tg_ui.PRIMARY, callback_data="countries")]]
    ))


# ─── اهدای کانفیگ توسط کاربران ─────────────────────────────
# درخواست کاربر: بخش اهدا، و اهدایی‌ها *اضافه بر* سهمیه‌ی ۱۰تایی هر دوره پست
# شوند (PUBLISH_DONATED_COUNT در هر دوره) و هیچ کانفیگی بیش از یک بار نرود.
#
# حریم خصوصی (خواسته‌ی صریح کاربر: «داده‌ی هیچ کاربری لو نرود»):
#   • شناسه‌ی تلگرام هیچ‌جا روی دیسک نمی‌رود — فقط hash نمکی‌اش برای سهمیه.
#   • اسمی که اهداکننده روی کانفیگ گذاشته دور ریخته می‌شود؛ کانال اسم خودش
#     را می‌سازد. پس متن کاربر هیچ‌وقت در پیام رندرشده نمی‌نشیند.
#   • متن اهدا لاگ نمی‌شود؛ فقط شمارش.

DONATE_FLAG = "donate_mode"        # فقط در حافظه‌ی PTB، روی دیسک نمی‌رود
DONATE_PENDING = "donate_pending"


def donate_intro() -> str:
    lines = [
        "🎁 *اهدای کانفیگ*",
        "─" * 26,
        "کانفیگ سالمی داری؟ بفرست تا در کانال با برچسب «اهدایی» منتشر شود.",
        "",
        f"📤 هر دوره *{PUBLISH_DONATED_COUNT}* کانفیگ اهدایی — *اضافه بر* "
        f"{PUBLISH_COUNT} کانفیگ همیشگی. بقیه در صف می‌مانند.",
        "🔁 هر کانفیگ *فقط یک بار* پست می‌شود.",
        "",
        "📋 *قواعد:*",
        f"  • حداکثر {DONATE_MAX_PER_MSG} کانفیگ در هر پیام"
        + (f"، {DONATE_MAX_PER_DAY} در روز" if DONATE_MAX_PER_DAY else ""),
        "  • فقط `vless://` — با دامنه یا IP عمومی",
        "  • تکراری و کانفیگ خراب خودکار رد می‌شود",
        "",
        "🔒 *حریم خصوصی:* شناسه‌ی تلگرامت ذخیره نمی‌شود (فقط hash برای سهمیه) "
        "و اسمی که روی کانفیگ گذاشته‌ای دور ریخته می‌شود.",
        "",
        "👇 حالا کانفیگ‌ها را در یک پیام بفرست (چند خط اشکالی ندارد).",
    ]
    return "\n".join(lines)


@user_command
async def cmd_donate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not DONATE_ENABLED:
        await reply(update, "⚠️ بخش اهدا موقتاً بسته است.")
        return
    inline = " ".join(context.args) if context.args else ""
    if vless.extract_configs(inline):
        await process_donation(update, context, inline)
        return
    if context.user_data is not None:
        context.user_data[DONATE_FLAG] = True
    queued = donations.queued_count()
    text = donate_intro()
    if queued:
        text += f"\n\n🗂 صف فعلی: *{queued}* کانفیگ در انتظار انتشار."
    await reply(update, text)


async def process_donation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    """اعتبارسنجی و افزودن به صف. هیچ‌جا متن کاربر لاگ نمی‌شود."""
    user = update.effective_user
    if not DONATE_ENABLED:
        await reply(update, "⚠️ بخش اهدا موقتاً بسته است.")
        return
    # سقف استخراج: پیام غول‌آسا نباید حلقه‌ی اعتبارسنجی را طولانی کند.
    configs = vless.extract_configs(text, limit=max(1, DONATE_MAX_PER_MSG) * 3)
    if not configs:
        await reply(
            update,
            "❌ لینک `vless://` در پیامت پیدا نشد.\n"
            "هر کانفیگ را در یک خط بفرست.",
        )
        return

    result = donations.add(configs, user_id=user.id if user else None)
    if context.user_data is not None:
        context.user_data.pop(DONATE_FLAG, None)
        context.user_data.pop(DONATE_PENDING, None)

    if result["blocked"]:
        await reply(
            update,
            f"⏳ {tg_md.strip_md(result['blocked'], 120)}\n"
            f"🗂 صف فعلی: {result['queued_total']} کانفیگ.",
        )
        return

    lines = ["🎁 *نتیجه‌ی اهدا*", ""]
    if result["added"]:
        lines.append(f"✅ پذیرفته شد: *{result['added']}*")
    if result["duplicate"]:
        lines.append(f"🔁 تکراری (قبلاً در صف/منتشرشده): {result['duplicate']}")
    if result["invalid"]:
        lines.append(f"❌ نامعتبر: {result['invalid']}")
    reasons = result["reasons"] if isinstance(result["reasons"], dict) else {}
    for reason, n in reasons.items():
        lines.append(f"   • {tg_md.strip_md(reason, 60)} ×{n}")
    lines.append("")
    lines.append(f"🗂 صف: *{result['queued_total']}* کانفیگ در انتظار")
    if result["added"]:
        cycles = int(result["queued_total"]) // max(1, PUBLISH_DONATED_COUNT)
        lines.append(
            f"📤 هر {PUBLISH_INTERVAL_MIN} دقیقه {PUBLISH_DONATED_COUNT} تا "
            f"منتشر می‌شود" + (f" (~{cycles} دوره)" if cycles > 1 else "")
        )
        lines.append("🙏 ممنون — هر کانفیگ فقط یک بار پست می‌شود.")
    else:
        lines.append("_چیزی به صف اضافه نشد._")
    await reply(update, "\n".join(lines))
    # فقط شمارش لاگ می‌شود، نه محتوا و نه شناسه‌ی کاربر.
    logger.info(
        f"🎁 اهدا: +{result['added']} | تکراری {result['duplicate']} | "
        f"نامعتبر {result['invalid']}"
    )


@user_command
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *راهنمای کامل*\n\n"
        "🔹 /get — بهترین کانفیگ\n"
        "🔹 /iran — فقط تأییدشده‌های داخل ایران 🇮🇷\n"
        "🔹 /country [کد] — انتخاب بر اساس کشور 🌍\n"
        "🔹 /list — لیست همه‌ی کانفیگ‌ها\n"
        "🔹 /reality — فقط Reality\n"
        "🔹 /tls — فقط TLS\n"
        "🔹 /random — یک کانفیگ رندوم\n"
        "🔹 /qr [شماره] — QR code کانفیگ\n"
        "🔹 /ping — تأخیر کانفیگ‌ها\n"
        "🔹 /sub — لینک اشتراک\n"
        "🔹 /donate — اهدای کانفیگ به کانال 🎁\n"
        "🔹 /stats — آمار کامل pipeline\n"
        "🔹 /whoami — شناسه و نقش من\n\n"
        "📌 *برچسب‌های روی هر کانفیگ:*\n"
        "`84ms` تأخیر واقعی تونل | `P0%` افت بسته | `J9ms` لرزش | "
        "`S430KB` سرعت دانلود | `IR212` جواب از ایران | `♻CF` احیا با IP تمیز\n"
        "برچسبی که نیست یعنی *اندازه‌گیری نشد* — نه «خوب» و نه «بد».\n\n"
        "💡 *کانفیگ‌ها از ۷ لایه رد شده‌اند:*\n"
        "فرمت → حذف تکراری → TCP → *دسترسی از ایران* → TLS → Geo → "
        "تأخیر واقعی + پایداری + سرعت\n\n"
        "🇮🇷 لایه‌ی «دسترسی از ایران» با نودهای ایرانی check-host تست می‌شود؛ "
        "بقیه‌ی لایه‌ها از سرور آمریکا اجرا می‌شوند و فقط سالم بودن سرور را "
        "می‌سنجند. کانفیگ‌های /iran بالاترین شانس باز شدن را دارند.\n\n"
        f"🔁 کانال هر {PUBLISH_INTERVAL_MIN} دقیقه {PUBLISH_COUNT} کانفیگ تازه "
        f"می‌گذارد (+ {PUBLISH_DONATED_COUNT} اهدایی) و پیام آخر هر دسته لینک "
        "اشتراک است."
    )
    user = update.effective_user
    if is_admin(user.id if user else None):
        text += "\n\n" + admin_help()
    await reply(update, text,
                reply_markup=keyboard_for(user.id if user else None))


def admin_help() -> str:
    """بخش ادمینِ /help — فقط برای ادمین به متن اضافه می‌شود.

    دستورهای ادمین در setup_commands هم فقط به chat خود ادمین‌ها معرفی
    می‌شوند، پس کاربر عادی نه در منوی تلگرام می‌بیندشان و نه اینجا.
    """
    return (
        "🛠️ *دستورهای ادمین*\n"
        "🔸 /publish — یک دوره‌ی انتشار همین حالا\n"
        "🔸 /cycle — گزارش ۵ دوره‌ی آخر\n"
        "🔸 /status — وضعیت ربات و پول‌ها\n"
        "🔸 /quality — کیفیت اندازه‌گیری‌شده‌ی پول (افت/لرزش/سرعت/احیا)\n"
        "🔸 /health — چرا آخرین اجرا خروجی نداشت\n"
        "🔸 /pause و /resume — مکث/ادامه‌ی *انتشار خودکار*\n"
        "🔸 /on و /off — روشن/خاموش کردن پاسخ به کاربران\n"
        "🔸 /donations — صف اهدا (+ `requeue` و `purge`)\n"
        "🔸 /run — اجرای pipeline در GitHub Actions\n"
        "🔸 /add — افزودن کانفیگ دستی\n"
        "🔸 /test — تست کامل یک کانفیگ (تا داخل تونل)\n"
        "🔸 /whoami — شناسه و نقش خودت"
    )


# ─── دکمه‌های ثابت → همان هندلرها ──────────────────────────

BUTTON_ROUTES: Dict[str, Callable] = {
    BTN_BEST: cmd_get,
    BTN_IRAN: cmd_iran,
    BTN_COUNTRY: cmd_country,
    BTN_RANDOM: cmd_random,
    BTN_LIST: cmd_list,
    BTN_SUB: cmd_sub,
    BTN_DONATE: cmd_donate,
    BTN_STATS: cmd_stats,
    BTN_QR: cmd_qr,
    BTN_HELP: cmd_help,
}

# دکمه‌های ادمین به هندلرهایی وصل می‌شوند که پایین‌تر تعریف شده‌اند، پس این
# نقشه بعد از تعریفشان پر می‌شود (پایین فایل). handle_button فقط در زمان
# اجرا نگاهش می‌کند، پس خالی بودنش در لحظه‌ی import مشکلی نیست.
ADMIN_ROUTES: Dict[str, Callable] = {}


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """متن دکمه‌های ثابت را به دستور معادلش می‌رساند.

    دکمه‌ی ReplyKeyboard یک پیام متنی معمولی می‌فرستد (نه callback)، پس
    مسیردهی روی متن انجام می‌شود.

    پیامی که لینک vless دارد دو معنا می‌تواند داشته باشد (اهدا، یا تست ادمین)،
    پس هیچ‌وقت خودکار اهدا نمی‌شود: یا کاربر قبلش دکمه‌ی اهدا را زده (پرچم
    donate_mode)، یا دکمه‌ی تأیید را می‌بیند. اهدای ناخواسته یعنی کانفیگ
    شخصیِ کسی در کانال عمومی — همان چیزی که نباید بشود.

    دکمه‌های ادمین هم از همین‌جا رد می‌شوند ولی هندلرشان `@admin_only` است؛
    اگر کاربر عادی متنشان را دستی تایپ کند، همان «⛔ فقط ادمین» را می‌گیرد.
    """
    message = update.effective_message
    text = (message.text or "").strip() if message else ""
    handler = BUTTON_ROUTES.get(text) or ADMIN_ROUTES.get(text)
    if handler is not None:
        await handler(update, context)
        return

    if vless.extract_configs(text):
        armed = bool(context.user_data and context.user_data.get(DONATE_FLAG))
        if armed and DONATE_ENABLED:
            await process_donation(update, context, text)
            return
        if DONATE_ENABLED:
            # متن فقط در حافظه‌ی همین کاربر می‌ماند تا اگر تأیید کرد استفاده
            # شود؛ روی دیسک نمی‌رود و با restart از بین می‌رود.
            if context.user_data is not None:
                context.user_data[DONATE_PENDING] = text[:8000]
            found = len(vless.extract_configs(text))
            await reply(
                update,
                f"📥 *{found}* لینک `vless://` در پیامت دیدم.\n"
                "می‌خواهی به کانال اهدا شود؟",
                reply_markup=InlineKeyboardMarkup([[
                    tg_ui.ikb("🎁 بله، اهدا کن", tg_ui.SUCCESS,
                              callback_data="donate_go"),
                    tg_ui.ikb("✖️ نه", tg_ui.DANGER, callback_data="donate_no"),
                ]]),
            )
            return
        await reply(update, "🧪 برای تست یک کانفیگ: `/test <لینک>` (فقط ادمین)")
        return

    # متن ناشناس: راهنمای کوتاه، همراه با کیبورد. اگر کاربر با آیکنِ تلگرام
    # دکمه‌ها را بسته باشد، همین پاسخ برشان می‌گرداند — و /help هم گفته شده،
    # پس کسی که دکمه نمی‌خواهد بی‌راه نمی‌ماند.
    await reply(update, "👇 از دکمه‌های پایین استفاده کن یا /help را بزن.",
                reply_markup=keyboard_for(
                    update.effective_user.id if update.effective_user else None))


# ─── انتشار در کانال ──────────────────────────────────────

async def _publish_once(bot, trigger: str = "auto") -> Dict:
    """یک دوره‌ی انتشار: ۱۰ کارت + اهدایی‌ها + پیام لینک اشتراک.

    هم حلقه‌ی خودکار و هم /publish از همین می‌گذرند تا رفتارشان یکی بماند
    (چرخش، cooldown و ثبت وضعیت همه در publisher انجام می‌شود).

    سه منبع به publisher داده می‌شود:
      • پول تأییدشده (`load_publish_pool`) — داخلی + بین‌المللی با هم. اینجا
        هیچ فیلتر «فقط ایران» نیست؛ سهمیه‌ی ۱۰تایی نباید بشکند.
      • پول ذخیره (`load_pool_configs`) — تست‌نشده‌ها، فقط اگر سهمیه کم آمد.
      • صف اهدای کاربران — *اضافه بر* سهمیه، حداکثر PUBLISH_DONATED_COUNT.

    اهدایی‌ها فقط بعد از ارسال *موفق* sent علامت می‌خورند؛ ناموفق‌ها در وضعیت
    taken می‌مانند و خودکار دوباره پست نمی‌شوند — قرارداد «حداکثر یک بار».
    """
    empty: Dict = {"selected": 0, "sent": 0, "failed": 0, "ids": []}
    await refresh_cache(force=True)
    configs = load_publish_pool()
    reserve = load_pool_configs() if PUBLISH_FILL_FROM_POOL else []
    donated = (
        donations.take_for_cycle(PUBLISH_DONATED_COUNT) if DONATE_ENABLED else []
    )
    if not configs and not reserve and not donated:
        logger.warning("⚠️ دوره‌ی انتشار رد شد — پول کانفیگ خالی است")
        return {**empty, "trigger": trigger, "at": _now_utc()}

    result = await Publisher(bot=bot).publish_batch(
        configs, load_stats(), reserve=reserve, donated=donated,
    )

    # ثبت اهدایی‌های ارسال‌شده. اگر این ذخیره نشود، دوره‌ی بعد همان کانفیگ
    # دوباره برداشته نمی‌شود (taken مانده) پس تکرار پیش نمی‌آید.
    sent_donations = [c for c in result.get("donated_sent", []) if isinstance(c, str)]
    if sent_donations:
        donations.mark_sent(sent_donations)

    result["trigger"] = trigger
    result["at"] = _now_utc()
    result["pool_size"] = len(configs)
    result["reserve_size"] = len(reserve)
    result["donated"] = len(sent_donations)
    _publish_log.append(dict(result))
    del _publish_log[:-5]
    return result


def _now_utc() -> str:
    return time.strftime("%H:%M UTC", time.gmtime())


async def auto_publish_loop(app: Application) -> None:
    """حلقه‌ی انتشار — عمداً asyncio خام.

    APScheduler نصب نیست (و JobQueue تلگرام به آن نیاز دارد)؛ این حلقه هیچ
    وابستگی‌ای اضافه نمی‌کند. یک استثنا در یک دوره، حلقه را نمی‌کشد.

    دو دکمه‌ی ادمین می‌توانند جلویش را بگیرند و حلقه *زنده* بماند: خاموشی کل
    ربات (BOT_ENABLED) و مکث انتشار (PUBLISH_PAUSED). حلقه کشته نمی‌شود چون
    /resume باید بدون restart کار کند؛ فقط این تیک را رد می‌کند.
    """
    interval = max(1, PUBLISH_INTERVAL_MIN) * 60
    await asyncio.sleep(20)   # تا اتصال ربات و اولین cache کامل شود
    while True:
        try:
            if not BOT_ENABLED:
                logger.info("⏸️ تیک انتشار رد شد — ربات خاموش است")
            elif PUBLISH_PAUSED:
                logger.info("⏸️ تیک انتشار رد شد — انتشار در حالت مکث")
            else:
                await _publish_once(app.bot, "auto")
        except asyncio.CancelledError:
            logger.info("⏹️ حلقه‌ی انتشار متوقف شد")
            raise
        except Exception as exc:
            logger.error(f"❌ دوره‌ی انتشار ناموفق: {exc}", exc_info=True)
        await asyncio.sleep(interval)


# ─── دستورات ادمین ────────────────────────────────────────

@admin_only
async def cmd_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """یک دوره‌ی انتشار دستی، بدون انتظار تا تیک بعدی."""
    await reply(update, "📤 در حال انتشار یک دسته...")
    result = await _publish_once(context.bot, "دستی")
    lines = [
        f"{'✅' if result['sent'] else '⚠️'} انتشار: "
        f"{result['selected']}/{PUBLISH_COUNT} کانفیگ انتخاب شد | "
        f"{result['sent']} پیام موفق | {result['failed']} ناموفق",
    ]
    if result.get("from_pool"):
        lines.append(f"🗃 {result['from_pool']} از پول ذخیره (تست‌نشده)")
    if result.get("donated"):
        lines.append(f"🎁 {result['donated']} کانفیگ اهدایی")
    if result.get("quota_short"):
        lines.append(
            f"⚠️ سهمیه {result['quota_short']} تا کم آمد — پول تأییدشده "
            f"{result.get('pool_size', 0)}، ذخیره {result.get('reserve_size', 0)}"
        )
    await reply(update, "\n".join(lines))


@admin_only
async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """trigger کردن workflow جمع‌آوری."""
    if not (GITHUB_TOKEN and GITHUB_REPO):
        await reply(update, "⚠️ GITHUB_TOKEN یا GITHUB_REPO تنظیم نشده.")
        return
    await reply(update, "🚀 در حال trigger کردن pipeline...")
    url = (
        f"https://api.github.com/repos/{GITHUB_REPO}"
        "/actions/workflows/collect.yml/dispatches"
    )
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                headers={
                    "Authorization": f"token {GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                },
                json={"ref": "main"},
            ) as resp:
                if resp.status == 204:
                    await reply(update, "✅ Pipeline شروع شد!")
                else:
                    body = tg_md.strip_md(await resp.text(), 150)
                    await reply(update, f"⚠️ HTTP {resp.status}\n{body}")
    except Exception as exc:
        await reply(update, f"❌ خطا: {tg_md.strip_md(exc, 150)}")


@admin_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await refresh_cache()
    configs = load_configs()
    iran = load_iran_configs()
    stats = load_stats()
    age = int(time.time() - _last_fetch) if _last_fetch else -1
    index = _cache["index"] if isinstance(_cache["index"], dict) else {}

    lines = [
        "🖥️ *وضعیت سیستم*",
        "",
        "🟢 روشن" if BOT_ENABLED else "🔴 خاموش",
        f"✅ {len(configs)} کانفیگ | 🇮🇷 {len(iran)}",
        f"🕐 {tg_md.code(str(stats.get('timestamp', '—'))[:19])}",
        f"🔄 cache: {age if age >= 0 else '—'}s پیش "
        f"({len(_cache['configs'])} از گیت‌هاب)",
        f"📇 index.json: {'✅ schema ' + str(index.get('schema')) if index else '❌'}",
        f"🔁 انتشار خودکار: "
        f"{'🟢 هر ' + str(PUBLISH_INTERVAL_MIN) + ' دقیقه' if AUTO_PUBLISH else '🔴 خاموش'}"
        f"{' — ⏸️ مکث دستی' if PUBLISH_PAUSED else ''}",
        f"🎁 صف اهدا: {donations.queued_count()} در انتظار",
    ]
    if _publish_log:
        lines.append("")
        lines.append("📤 *آخرین دوره‌های انتشار:*")
        for item in reversed(_publish_log):
            lines.append(
                f"  {item.get('at', '—')} ({item.get('trigger', '—')}): "
                f"{item.get('sent', 0)}✅ / {item.get('failed', 0)}❌"
            )
    await reply(update, "\n".join(lines))


def _quality_report(configs: List[str]) -> List[str]:
    """گزارش کیفیتِ *اندازه‌گیری‌شده‌ی* پول — بدون هیچ تست تازه.

    هر عدد این‌جا از برچسب خودِ کانفیگ خوانده می‌شود، پس گزارش رایگان است و
    به شبکه دست نمی‌زند. دسته‌ی «اندازه‌گیری‌نشده» جدا شمرده می‌شود و در
    میانگین‌ها نمی‌آید؛ وگرنه پول ذخیره میانگین افت را مصنوعی صفر می‌کرد.
    """
    if not configs:
        return ["_پول خالی است._"]

    losses = [v for v in (vless.get_loss_pct(c) for c in configs) if v >= 0]
    jitters = [v for v in (vless.get_jitter_ms(c) for c in configs) if v >= 0]
    speeds = [v for v in (vless.get_speed_kbps(c) for c in configs) if v > 0]
    latencies = [
        v for v in (vless.get_latency_ms(c) for c in configs)
        if v and v != float("inf")
    ]
    stable = sum(1 for v in losses if v == 0)
    revived = sum(1 for c in configs if REVIVE_MARK in vless.split_fragment(c)[1])
    iran = sum(1 for c in configs if vless.is_iran_verified(c))
    unmeasured = len(configs) - len(losses)

    def avg(values: List[float]) -> float:
        return round(sum(values) / len(values), 1) if values else 0.0

    lines = [
        f"📦 {len(configs)} کانفیگ | 🇮🇷 {iran} تأییدشده از ایران",
        # «۰ از —» یعنی هیچ، ولی خوانده می‌شود «هیچ‌کدام سالم نیستند». وقتی
        # چیزی اندازه‌گیری نشده، همان را بگو — قاعده‌ی بقیه‌ی خطوط هم همین است.
        (
            f"💚 بدون افت بسته: {stable}/{len(losses)}"
            f" | میانگین افت {avg(losses)}%"
        ) if losses else "💚 افت بسته: اندازه‌گیری نشد",
        f"📶 لرزش: میانگین {avg(jitters)}ms ({len(jitters)} اندازه‌گیری)"
        if jitters else "📶 لرزش: اندازه‌گیری نشد",
        f"⬇️ سرعت: میانگین {avg(speeds)} KB/s ({len(speeds)} اندازه‌گیری)"
        if speeds else "⬇️ سرعت: اندازه‌گیری نشد",
        f"⚡️ تأخیر تونل: میانگین {avg(latencies)}ms"
        + (f" | بهترین {round(min(latencies))}ms" if latencies else ""),
        f"♻️ احیاشده با IP تمیز کلودفلر: {revived}",
    ]
    if unmeasured:
        lines.append(
            f"🗃 بدون سنجه‌ی پایداری: {unmeasured} "
            "(تست‌نشده یا اهدایی — «تست نشد» ≠ «رد شد»)"
        )
    return lines


# کلیدی که فقط main() نسخه‌ی سنجه‌دار در summary می‌نویسد. بودن/نبودنش
# می‌گوید فایل‌های خروجی با کدام نسخه ساخته شده‌اند — دقیق‌تر از حدس زدن.
_QUALITY_MARK = "speed_measured"


def _quality_gap(configs: List[str], stats: Dict) -> List[str]:
    """اگر هیچ سنجه‌ای در پول نیست، *دلیلش* را بگو.

    شکایت کاربر: «دکمه‌ی کیفیت پول اگر کار نکند بی‌فایده است.» گزارش خودش
    درست کار می‌کرد؛ چیزی که ادمین می‌دید سه خطِ «اندازه‌گیری نشد» بود بی هیچ
    توضیحی — و از آن نمی‌شد فهمید ربات خراب است یا داده. سنجه‌ها از برچسبِ
    خودِ کانفیگ خوانده می‌شوند، پس خالی بودنشان یعنی *فایل خروجی* سنجه ندارد،
    و دلیلش در stats.json هست. این تابع همان دلیل را با گام بعدی می‌نویسد.
    """
    if not configs or any(vless.get_loss_pct(c) >= 0 for c in configs):
        return []

    pipeline = (stats.get("pipeline") or {})
    layer7 = pipeline.get("layer7_http") or {}
    summary = pipeline.get("summary") or {}
    lines = ["", "🔎 *چرا سنجه‌ای نیست*"]

    if stats.get("skip_xray") or layer7.get("skipped"):
        lines.append(
            "لایه ۷ با SKIP_XRAY رد شده — افت/لرزش/سرعت فقط داخل تونل xray "
            "اندازه‌گیری می‌شوند. برای داشتنشان SKIP_XRAY=0 و بعد /run."
        )
    elif not layer7:
        lines.append(
            "آخرین اجرا به لایه ۷ نرسید (لایه‌های قبل خالی شدند یا خطا خورد). "
            "/health دلیل را می‌گوید، /run اجرای تازه می‌سازد."
        )
    elif summary and _QUALITY_MARK not in summary:
        lines.append(
            "این فایل‌ها *قبل از* اضافه شدن اندازه‌گیری پایداری ساخته شده‌اند، "
            "پس برچسب سنجه ندارند. اولین /run بعدی درستش می‌کند."
        )
    elif not layer7.get("passed"):
        lines.append(
            f"لایه ۷ هیچ کانفیگی را تأیید نکرد (از {layer7.get('total', 0)} "
            "ورودی)، پس این پول از ذخیره‌ی تست‌نشده پر شده."
        )
    else:
        lines.append(
            "لایه ۷ اجرا شده ولی برچسبی روی کانفیگ‌ها نیست — یعنی فایل‌های "
            "کانفیگ و stats.json از یک اجرا نیستند. /run هم‌ترازشان می‌کند."
        )

    when = str(stats.get("timestamp", ""))[:16].replace("T", " ")
    if when:
        lines.append(f"🕐 داده‌ی این گزارش از اجرای {when} است.")
    return lines


@admin_only
async def cmd_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کیفیت پول از دید سنجه‌های لایه ۷ — افت بسته، لرزش، سرعت، احیا.

    /status می‌گوید «چند کانفیگ داریم»؛ این می‌گوید «چقدر خوب‌اند». هر دو
    پول جدا گزارش می‌شوند چون فقط پول تأییدشده سنجه دارد و اگر با ذخیره قاطی
    شود، همان ادعای دروغِ «۰٪ افت» ساخته می‌شود که در کارت‌ها جلویش گرفته شد.
    """
    await refresh_cache()
    configs = load_configs()
    stats = load_stats()
    lines = ["📶 *کیفیت پول تأییدشده*", ""]
    lines += _quality_report(configs)
    # پول خالی دلیلِ خودش را دارد («_پول خالی است._»)؛ تشخیصِ سنجه فقط وقتی
    # معنی دارد که کانفیگ هست ولی برچسبی روی آن نیست.
    lines += _quality_gap(configs, stats)

    reserve = load_pool_configs()
    lines += ["", f"🗃 *پول ذخیره (تست‌نشده)* — {len(reserve)} کانفیگ"]
    if reserve:
        lines.append(
            "این‌ها لایه ۶ را پاس کردند ولی به سقف زمانی لایه ۷ خوردند، پس "
            "سنجه‌ی پایداری ندارند و در کانال با برچسب «تست‌نشده» می‌روند."
        )
    summary = (stats.get("pipeline") or {}).get("summary") or {}
    if isinstance(summary, dict) and summary:
        lines += [
            "",
            "🔬 *آخرین اجرای pipeline*",
            f"funnel: {' → '.join(str(n) for n in summary.get('funnel', []))}",
            f"💚 بدون افت: {summary.get('stable', 0)} | "
            f"⚡ میانگین سرعت: {summary.get('avg_speed_kbps', 0)} KB/s | "
            f"♻️ احیا: {summary.get('revived', 0)}",
        ]
        # چرخشِ نوبتِ لایه ۷ — تنها عددِ قابل‌اندازه‌گیری در پاسخ به «ربات دیگر
        # کانفیگ تازه پیدا نمی‌کند». اگر کلید نباشد (اجرای SKIP_XRAY یا خروجیِ
        # قبل از این نسخه) خطی نوشته نمی‌شود: «۰ کانفیگِ تازه» یک ادعای
        # اندازه‌گیری‌شده است و بی‌اندازه‌گیری نوشتنش همان دروغِ همیشگی.
        if "fresh_tested" in summary or "new_passed" in summary:
            lines.append(
                f"🆕 اولین‌بار از تونل آزموده شد: {summary.get('fresh_tested', 0)}"
                f" | تازه تأیید شد: {summary.get('new_passed', 0)}"
            )
    await reply(update, "\n".join(lines))


def _state_note(saved: bool) -> str:
    """اگر ذخیره نشد، ادمین باید بداند که با restart برمی‌گردد."""
    return "" if saved else "\n⚠️ روی دیسک ذخیره نشد — با restart به حالت قبل برمی‌گردد."


@admin_only
async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """روشن کردن پاسخ به کاربران عادی (ادمین همیشه دسترسی دارد)."""
    global BOT_ENABLED
    BOT_ENABLED = True
    saved = save_bot_state()
    logger.info("🟢 ربات روشن شد (دستور ادمین)")
    await reply(update, "🟢 ربات روشن شد!" + _state_note(saved))


@admin_only
async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خاموش کردن پاسخ به کاربران عادی.

    انتشار خودکار هم متوقف می‌شود (حلقه BOT_ENABLED را می‌بیند) ولی /publish
    دستی کار می‌کند. برای مکثِ فقط‌انتشار، /pause سبک‌تر است.
    """
    global BOT_ENABLED
    BOT_ENABLED = False
    saved = save_bot_state()
    logger.info("🔴 ربات خاموش شد (دستور ادمین)")
    await reply(
        update,
        "🔴 ربات خاموش شد — کاربران عادی پاسخ نمی‌گیرند و انتشار خودکار هم "
        "متوقف است.\nدسترسی خودت باز است: /on برای برگشت." + _state_note(saved),
    )


@admin_only
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مکث انتشار خودکار — ربات به کاربران پاسخ می‌دهد."""
    global PUBLISH_PAUSED
    PUBLISH_PAUSED = True
    saved = save_bot_state()
    logger.info("⏸️ انتشار خودکار مکث شد (دستور ادمین)")
    await reply(
        update,
        "⏸️ انتشار خودکار مکث شد. ربات به کاربران پاسخ می‌دهد و /publish "
        "دستی هم کار می‌کند.\n/resume برای ادامه." + _state_note(saved),
    )


@admin_only
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادامه‌ی انتشار خودکار از تیک بعدی."""
    global PUBLISH_PAUSED
    PUBLISH_PAUSED = False
    saved = save_bot_state()
    logger.info("▶️ انتشار خودکار ادامه یافت (دستور ادمین)")
    await reply(
        update,
        f"▶️ انتشار خودکار ادامه یافت — تیک بعدی حداکثر تا "
        f"{PUBLISH_INTERVAL_MIN} دقیقه.\nبرای انتشار فوری: /publish"
        + _state_note(saved),
    )


@admin_only
async def cmd_toggle_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دکمه‌ی «مکث/ادامه» — یک دکمه برای هر دو حالت."""
    if PUBLISH_PAUSED:
        await cmd_resume(update, context)
    else:
        await cmd_pause(update, context)


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شناسه و نقش — تنها راه امن برای پیدا کردن مقدار ADMIN_IDS.

    عمداً بدون @admin_only: کاربر عادی هم باید بتواند شناسه‌ی خودش را
    ببیند (مثلاً برای درخواست دسترسی). فقط شناسه‌ی *خودش* را می‌بیند.
    """
    user = update.effective_user
    uid = user.id if user else None
    admin = is_admin(uid)
    lines = [
        "🪪 *شناسه‌ی تو*",
        f"🆔 {tg_md.code(str(uid) if uid else '—')}",
        f"👤 نقش: {'🛠️ ادمین' if admin else '🙋 کاربر عادی'}",
    ]
    if not admin:
        lines.append("")
        lines.append(
            "_برای دسترسی ادمین، همین شناسه باید در ADMIN_IDS سرور اضافه شود._"
        )
    await reply(update, "\n".join(lines))


@admin_only
async def cmd_donations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """آمار صف اهدا + دو عمل نگهداری.

    `/donations requeue` فقط برای وقتی است که پروسه بین برداشت و ارسال مرده
    باشد؛ خودکار نیست چون ممکن است واقعاً پست شده باشد و قرارداد «حداکثر یک
    بار» بشکند. `/donations purge [n]` رکوردهای ارسال‌شده‌ی قدیمی را هرس
    می‌کند (خودِ کانفیگ‌ها در کانال می‌مانند).
    """
    args = context.args or []
    action = (args[0].lower() if args else "")

    if action == "requeue":
        moved = donations.requeue_taken()
        logger.info(f"🎁 برگشت به صف: {moved}")
        await reply(
            update,
            f"↩️ {moved} کانفیگ از «برداشته‌شده» به صف برگشت.\n"
            "_اگر واقعاً پست شده بودند، دوباره پست می‌شوند — با احتیاط._",
        )
        return

    if action == "purge":
        keep = 500
        if len(args) > 1 and args[1].isdigit():
            keep = max(0, int(args[1]))
        removed = donations.purge_sent(keep=keep)
        logger.info(f"🎁 هرس رکوردهای ارسال‌شده: {removed} (نگه‌داشت {keep})")
        await reply(update, f"🧹 {removed} رکورد ارسال‌شده حذف شد (نگه‌داشت {keep}).")
        return

    stats = donations.stats()
    lines = [
        "🎁 *صف اهدا*",
        "",
        f"⏳ در انتظار: {stats['queued']}",
        f"📤 برداشته‌شده (ارسال‌نشده): {stats['taken']}",
        f"✅ ارسال‌شده: {stats['sent']}",
        f"🗂 کل رکوردها: {stats['total']}",
        f"🙋 اهداکننده‌ها: {stats['donors']} (هش‌شده — شناسه ذخیره نمی‌شود)",
        f"🔁 دوره تا خالی شدن صف: {stats['cycles_left']} "
        f"({PUBLISH_DONATED_COUNT} در هر دوره)",
        "",
        f"وضعیت اهدا: {'🟢 باز' if DONATE_ENABLED else '🔴 بسته'} | "
        f"سقف روزانه هر کاربر: {DONATE_MAX_PER_DAY}",
        "",
        "_دستورها:_ `/donations requeue` — `/donations purge [تعداد نگه‌داشت]`",
    ]
    await reply(update, "\n".join(lines))


@admin_only
async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سلامت منابع — «کدام منبع مرده است».

    ساختار را `src/health.py::snapshot` می‌نویسد: by_kind، dead_sources،
    top_sources. اول از گیت‌هاب می‌خوانیم چون سرور ربات همان فایل‌سیستم
    رانر نیست؛ اگر نبود، نسخه‌ی محلی.
    """
    text = ""
    if RAW_BASE:
        text = await fetch_from_github(f"{RAW_BASE}/{HEALTH_FILE}")
    if not text:
        try:
            with open(HEALTH_FILE, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            text = ""
    if not text:
        await reply(update, "❔ فایل سلامت پیدا نشد — هنوز اجرایی ثبت نشده.")
        return
    try:
        data = json.loads(text)
    except ValueError:
        await reply(update, "⚠️ فایل سلامت خوانا نیست (JSON معتبر نبود).")
        return
    if not isinstance(data, dict):
        await reply(update, "⚠️ ساختار فایل سلامت غیرمنتظره بود.")
        return

    lines = [
        "🩺 *سلامت منابع*",
        "",
        f"🕐 {tg_md.code(str(data.get('updated_at', '—'))[:19])}",
    ]

    by_kind = data.get("by_kind")
    if isinstance(by_kind, dict) and by_kind:
        lines.append("")
        total_dead = 0
        for kind, bucket in by_kind.items():
            if not isinstance(bucket, dict):
                continue
            dead = int(bucket.get("dead", 0) or 0)
            total_dead += dead
            lines.append(
                f"📡 *{tg_md.strip_md(kind, 20)}*: {bucket.get('ok', 0)}✅ "
                f"{dead}❌ از {bucket.get('sources', 0)} منبع — "
                f"{bucket.get('configs', 0)} کانفیگ"
            )
        if total_dead:
            lines.append(f"\n💀 مجموع منابع مرده: {total_dead}")

    dead_sources = data.get("dead_sources")
    if isinstance(dead_sources, list) and dead_sources:
        lines.append("")
        lines.append("💀 *منابع مرده* (۱۰ مورد اول):")
        for item in dead_sources[:10]:
            if not isinstance(item, dict):
                continue
            name = tg_md.strip_md(str(item.get("name", "?")), 60)
            err = tg_md.strip_md(str(item.get("error", "")), 60)
            lines.append(f"  • {name} — {err}")
        if len(dead_sources) > 10:
            lines.append(f"  _… و {len(dead_sources) - 10} منبع دیگر_")

    top = data.get("top_sources")
    if isinstance(top, list) and top:
        lines.append("")
        lines.append("🏆 *پربارترین منابع:*")
        for item in top[:5]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"  • {tg_md.strip_md(str(item.get('name', '?')), 60)}: "
                f"{item.get('count', 0)}"
            )
    await reply(update, "\n".join(lines))


@admin_only
async def cmd_cycle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گزارش دوره‌های انتشار — همان چیزی که مشکل «۳ کانفیگ» را نشان می‌دهد."""
    lines = [
        "🔁 *دوره‌های انتشار*",
        "",
        f"⚙️ خودکار: {'🟢 فعال' if AUTO_PUBLISH else '🔴 غیرفعال'}"
        f"{' — ⏸️ مکث' if PUBLISH_PAUSED else ''}"
        f" | هر {PUBLISH_INTERVAL_MIN} دقیقه",
        f"🎯 سهمیه: {PUBLISH_COUNT} کانفیگ + {PUBLISH_DONATED_COUNT} اهدایی",
        f"🗃 پر کردن از پول ذخیره: "
        f"{'🟢 بله' if PUBLISH_FILL_FROM_POOL else '🔴 خیر'}",
    ]
    if not _publish_log:
        lines += ["", "_هنوز دوره‌ای در این اجرا ثبت نشده._"]
        await reply(update, "\n".join(lines))
        return

    lines.append("")
    for item in reversed(_publish_log):
        lines.append(
            f"🕐 *{item.get('at', '—')}* ({item.get('trigger', '—')})\n"
            f"  انتخاب {item.get('selected', 0)}/{PUBLISH_COUNT} | "
            f"ارسال {item.get('sent', 0)}✅ {item.get('failed', 0)}❌\n"
            f"  پول {item.get('pool_size', 0)} | "
            f"ذخیره {item.get('reserve_size', 0)} | "
            f"از ذخیره {item.get('from_pool', 0)} | "
            f"اهدایی {item.get('donated', 0)}"
            + (f"\n  ⚠️ کمبود سهمیه: {item['quota_short']}"
               if item.get("quota_short") else "")
        )
    await reply(update, "\n".join(lines))


@admin_only
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """افزودن دستی کانفیگ به configs/manual.txt.

    نسخه‌ی قبلی valid.txt را با محتوای cache بازنویسی می‌کرد، یعنی خروجی
    pipeline پاک می‌شد و اجرای بعدی گیت‌هاب هم آن را overwrite می‌کرد؛
    کانفیگ دستی هر بار گم می‌شد.
    """
    if not context.args:
        await reply(update, "❌ کانفیگ رو بعد از /add بفرست.")
        return
    cfg = " ".join(context.args).strip()

    from src.tester.format_validator import validate_vless

    ok, reason = validate_vless(cfg)
    if not ok:
        await reply(update, f"❌ رد شد: {tg_md.strip_md(reason, 100)}")
        return

    try:
        existing = _read_local(MANUAL_FILE)
        if cfg in existing:
            await reply(update, "⚠️ این کانفیگ قبلاً در manual.txt هست.")
            return
        parent = os.path.dirname(MANUAL_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(MANUAL_FILE, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(cfg + "\n")
        await reply(
            update,
            f"✅ به {tg_md.code(MANUAL_FILE)} اضافه شد "
            f"({len(existing) + 1} کانفیگ دستی).",
        )
    except OSError as exc:
        await reply(update, f"❌ خطا: {tg_md.strip_md(exc, 120)}")


def _probe_lines(probe) -> List[str]:
    """خطوط لایه ۷ برای /test — تأخیر واقعی، افت، لرزش، سرعت.

    نبودِ xray روی هاست ربات ⚪ است نه ❌: کانفیگ رد نشده، فقط این‌جا قابل
    آزمودن نبوده. همان تفکیکی که در کارت مشخصات هم رعایت می‌شود.
    """
    if probe.reason == "xray پیدا نشد":
        return [
            "⚪ تونل: xray روی این هاست نیست — "
            "این لایه در GitHub Actions اجرا می‌شود (تست نشد ≠ رد شد)."
        ]
    head = (
        f"{'✅' if probe.ok else '❌'} تونل واقعی: "
        + (f"{round(probe.delay_ms)}ms  •  {renderer.quality(probe.delay_ms)}"
           if probe.delay_ms > 0 else tg_md.strip_md(probe.reason or "بی‌جواب", 70))
    )
    lines = [head]
    if not probe.ok and probe.delay_ms > 0 and probe.reason:
        lines.append(f"   ↳ دلیل رد: {tg_md.strip_md(probe.reason, 70)}")
    stability = renderer.stability_text(probe.loss_pct, probe.jitter_ms)
    lines.append(f"📶 پایداری: {stability}" if stability
                 else "⚪ پایداری: اندازه‌گیری نشد")
    speed = renderer.speed_text(probe.speed_kbps)
    lines.append(f"⬇️ سرعت: {speed}" if speed else "⚪ سرعت: اندازه‌گیری نشد")
    if probe.speed_only_fail:
        lines.append("ℹ️ همه‌ی probe ها را پاس کرد و فقط روی گیت سرعت افتاد.")
    return lines


@admin_only
async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تست کامل یک کانفیگ: فرمت → TCP → TLS → ایران → *داخل تونل*.

    لایه‌ی آخر همان چیزی است که کاربر خواست جای پینگ TCP بنشیند: ترافیک
    واقعی از داخل تونل تا یک endpoint‌ ۲۰۴، و از همان چند بسته افت/لرزش/سرعت
    درمی‌آید. روی هاست ربات معمولاً xray نیست؛ آن حالت ⚪ است نه ❌، چون
    «تست نشد» ≠ «رد شد» و ادمین نباید کانفیگ سالم را دور بیندازد.
    """
    if not context.args:
        await reply(update, "❌ کانفیگ رو بعد از /test بفرست.")
        return
    cfg = " ".join(context.args).strip()
    await reply(update, "🔄 در حال تست...")

    from src.tester.checkhost_tester import check_endpoint, endpoint_of
    from src.tester.format_validator import validate_vless
    from src.tester.http_tester import probe_config
    from src.tester.tcp_tester import tcp_connect
    from src.tester.tls_tester import test_tls_single

    lines = []
    ok, reason = validate_vless(cfg)
    lines.append(f"{'✅' if ok else '❌'} فرمت: {tg_md.strip_md(reason or 'OK', 80)}")

    if ok:
        info = vless.parse(cfg)
        host, port = info.host, (info.port or 443)
        tcp_ok, tcp_ms = await tcp_connect(host, port)
        lines.append(
            f"{'✅' if tcp_ok else '❌'} TCP: {tg_md.strip_md(host)}:{port} | {tcp_ms}ms"
        )
        if tcp_ok:
            tls_ok, tls_ms, tls_reason = await test_tls_single(cfg)
            lines.append(
                f"{'✅' if tls_ok else '❌'} TLS: "
                f"{tls_ms}ms {tg_md.strip_md(tls_reason, 60)}".strip()
            )
            # مهم‌ترین خط: از ایران باز می‌شود یا نه.
            try:
                timeout = aiohttp.ClientTimeout(total=40)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    nodes, iran_ms, iran_reason = await check_endpoint(
                        session, endpoint_of(cfg)
                    )
                lines.append(
                    f"{'✅' if nodes else '❌'} از ایران: "
                    f"{str(nodes) + ' نود | ' + str(round(iran_ms)) + 'ms' if nodes else tg_md.strip_md(iran_reason, 60)}"
                )
            except Exception as exc:
                lines.append(f"⚠️ check-host: {tg_md.strip_md(exc, 60)}")

            lines += _probe_lines(await probe_config(cfg))

    await reply(update, "🧪 *نتیجه تست*\n\n" + "\n".join(lines))


# ─── Callback Queries ─────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    user = update.effective_user
    if not BOT_ENABLED and not is_admin(user.id if user else None):
        await reply(update, "🔴 ربات موقتاً خاموشه.")
        return

    data = query.data or ""
    await refresh_cache(force=(data == "refresh"))
    configs = sort_best(load_configs())

    if data == "get" or data.startswith("get_next_"):
        if not configs:
            await reply(update, "❌ کانفیگی موجود نیست.")
            return
        index = 0
        if data.startswith("get_next_"):
            try:
                index = int(data.rsplit("_", 1)[-1])
            except ValueError:
                index = 0
        index %= len(configs)
        await reply(
            update,
            card(configs[index], index + 1, len(configs)),
            reply_markup=card_keyboard(index),
        )

    elif data == "random":
        if not configs:
            await reply(update, "❌ کانفیگی موجود نیست.")
            return
        index = random.randrange(len(configs))
        await reply(
            update,
            card(configs[index], index + 1, len(configs)),
            reply_markup=InlineKeyboardMarkup([[
                tg_ui.ikb("🎲 یکی دیگه", tg_ui.PRIMARY, callback_data="random"),
                tg_ui.ikb("📷 QR", tg_ui.PRIMARY, callback_data=f"qr_{index}"),
            ]]),
        )

    elif data == "iran":
        await cmd_iran(update, context)

    elif data == "countries":
        await cmd_country(update, context)

    elif data.startswith("cop_"):
        # صفحه‌ی بعدی/قبلی فهرست کشور. پیشوند جدا از `co_` است تا کد کشور و
        # شماره‌ی صفحه با هم قاطی نشوند.
        try:
            page = int(data[4:])
        except ValueError:
            page = 0
        await show_country_list(update, page)

    elif data.startswith("co_"):
        await show_country(update, data[3:])

    elif data == "donate":
        await cmd_donate(update, context)

    elif data == "donate_go":
        pending = (context.user_data or {}).get(DONATE_PENDING, "")
        if pending:
            await process_donation(update, context, pending)
        else:
            await cmd_donate(update, context)

    elif data == "donate_no":
        if context.user_data is not None:
            context.user_data.pop(DONATE_PENDING, None)
            context.user_data.pop(DONATE_FLAG, None)
        await reply(update, "باشه — چیزی اهدا نشد. 👌")

    elif data == "reality":
        await reply(update, format_config_list(
            [c for c in configs if vless.is_reality(c)], "🔐 Reality"
        ))

    elif data == "tls":
        await reply(update, format_config_list(
            [c for c in configs if vless.get_security_label(c) == "TLS"], "🔒 TLS"
        ))

    elif data == "sub":
        await cmd_sub(update, context)

    elif data == "stats":
        await cmd_stats(update, context)

    elif data == "refresh":
        await reply(update, f"🔄 *آپدیت شد*\n✅ {len(configs)} کانفیگ موجود")

    elif data.startswith("qr_"):
        target = data[3:]
        if target == "sub":
            payload, caption = SUB_URL, "لینک اشتراک"
        elif target == "random" and configs:
            index = random.randrange(len(configs))
            payload, caption = configs[index], f"کانفیگ #{index + 1}"
        else:
            try:
                index = int(target)
            except ValueError:
                index = 0
            if not configs or not 0 <= index < len(configs):
                payload, caption = "", ""
            else:
                payload, caption = configs[index], f"کانفیگ #{index + 1}"

        if payload:
            await send_qr(update, payload, caption)
        else:
            await reply(update, "❌ کانفیگی برای QR موجود نیست.")


# ─── راه‌اندازی ───────────────────────────────────────────

USER_COMMANDS = [
    ("start", "شروع", cmd_start),
    ("get", "بهترین کانفیگ", cmd_get),
    ("iran", "تأییدشده از داخل ایران", cmd_iran),
    ("country", "انتخاب بر اساس کشور", cmd_country),
    ("list", "لیست کانفیگ‌ها", cmd_list),
    ("reality", "کانفیگ‌های Reality", cmd_reality),
    ("tls", "کانفیگ‌های TLS", cmd_tls),
    ("random", "کانفیگ رندوم", cmd_random),
    ("qr", "QR code کانفیگ", cmd_qr),
    ("ping", "تأخیر کانفیگ‌ها", cmd_ping),
    ("sub", "لینک اشتراک", cmd_sub),
    ("donate", "اهدای کانفیگ", cmd_donate),
    ("stats", "آمار pipeline", cmd_stats),
    ("whoami", "شناسه و نقش من", cmd_whoami),
    ("help", "راهنما", cmd_help),
]

# ادمین‌ها: توضیح دارند چون در منوی تلگرام *فقط برای خودشان* ثبت می‌شوند
# (BotCommandScopeChat). کاربر عادی این‌ها را در منو نمی‌بیند — و اگر دستی
# تایپ کند، @admin_only جوابش را می‌دهد. منو راحتی است، مرز امنیت نیست.
ADMIN_COMMANDS = [
    ("publish", "انتشار یک دوره الان", cmd_publish),
    ("cycle", "گزارش دوره‌های انتشار", cmd_cycle),
    ("status", "وضعیت سیستم", cmd_status),
    ("quality", "کیفیت اندازه‌گیری‌شده‌ی پول", cmd_quality),
    ("health", "سلامت منابع", cmd_health),
    ("pause", "مکث انتشار خودکار", cmd_pause),
    ("resume", "ادامه‌ی انتشار خودکار", cmd_resume),
    ("on", "روشن کردن ربات", cmd_on),
    ("off", "خاموش کردن ربات", cmd_off),
    ("donations", "صف اهدا", cmd_donations),
    ("run", "اجرای pipeline", cmd_run),
    ("add", "افزودن کانفیگ دستی", cmd_add),
    ("test", "تست یک کانفیگ", cmd_test),
]

# نقشه‌ی دکمه‌های ادمین (بالا خالی تعریف شد، این‌جا پر می‌شود).
ADMIN_ROUTES.update({
    BTN_A_PUBLISH: cmd_publish,
    BTN_A_STATUS: cmd_status,
    BTN_A_DONATIONS: cmd_donations,
    BTN_A_TOGGLE: cmd_toggle_publish,
    BTN_A_QUALITY: cmd_quality,
    BTN_A_HEALTH: cmd_health,
})


async def setup_commands(app: Application) -> None:
    """منوی دستورها. خطا نباید جلوی بالا آمدن ربات را بگیرد.

    دو دامنه: عمومی (کاربر) و per-chat برای هر ادمین. اگر ثبت برای یک
    ادمین شکست بخورد (مثلاً هرگز به ربات پیام نداده و chat وجود ندارد)،
    بقیه‌ی ادمین‌ها و بالا آمدن ربات را خراب نمی‌کند.
    """
    try:
        await app.bot.set_my_commands(
            [BotCommand(name, desc) for name, desc, _ in USER_COMMANDS]
        )
        logger.info("✅ منوی دستورها ثبت شد")
    except Exception as exc:
        logger.warning(f"⚠️ ثبت منوی دستورها ناموفق: {exc}")

    admin_menu = [
        BotCommand(name, desc)
        for name, desc, _ in USER_COMMANDS + ADMIN_COMMANDS
    ]
    ok = 0
    for admin_id in ADMIN_IDS:
        try:
            await app.bot.set_my_commands(
                admin_menu, scope=BotCommandScopeChat(chat_id=admin_id)
            )
            ok += 1
        except Exception as exc:
            logger.warning(f"⚠️ منوی ادمین ثبت نشد: {exc}")
    if ok:
        logger.info(f"🛠️ منوی ادمین برای {ok} ادمین ثبت شد")


async def post_init(app: Application) -> None:
    load_bot_state()
    logger.info(tg_ui.support_note())
    logger.info(
        f"🛠️ {len(ADMIN_IDS)} ادمین | {len(USER_COMMANDS)} دستور کاربر | "
        f"{len(ADMIN_COMMANDS)} دستور ادمین"
    )
    await setup_commands(app)
    if not AUTO_PUBLISH:
        logger.info("⏸️ انتشار خودکار خاموش است (AUTO_PUBLISH=0)")
        return
    if not TELEGRAM_CHANNEL_ID:
        logger.warning("⚠️ TELEGRAM_CHANNEL_ID نیست — انتشار خودکار غیرفعال")
        return
    app.bot_data["publish_task"] = asyncio.create_task(auto_publish_loop(app))
    logger.info(
        f"🔁 انتشار خودکار فعال: هر {PUBLISH_INTERVAL_MIN} دقیقه، "
        f"{PUBLISH_COUNT} کانفیگ + لینک اشتراک"
        + (" — ⏸️ در حالت مکث شروع می‌شود" if PUBLISH_PAUSED else "")
    )


async def post_shutdown(app: Application) -> None:
    task = app.bot_data.get("publish_task")
    if task is not None:
        task.cancel()


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN تنظیم نشده")
        raise SystemExit(1)
    if not ADMIN_IDS:
        logger.warning("⚠️ ADMIN_IDS خالیه — دستورهای ادمین برای هیچ‌کس فعال نیست")
    if not RAW_BASE:
        logger.warning("⚠️ GITHUB_REPO تنظیم نشده — cache گیت‌هاب غیرفعاله")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    for name, _desc, handler in USER_COMMANDS:
        app.add_handler(CommandHandler(name, handler))
    for name, _desc, handler in ADMIN_COMMANDS:
        app.add_handler(CommandHandler(name, handler))
    app.add_handler(CallbackQueryHandler(handle_callback))
    # دکمه‌های ثابت متن معمولی می‌فرستند، پس آخرین هندلر متن را می‌گیرد.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button))

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    logger.info("🤖 ربات شروع شد")
    # drop_pending_updates: پیام‌های زمان خاموشی دوباره پردازش نمی‌شوند.
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
