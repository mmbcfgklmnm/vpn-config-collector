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
  • دکمه‌های ثابت پایین چت (ReplyKeyboardMarkup با is_persistent) — کاربر
    برای کارهای رایج چیزی تایپ نمی‌کند.
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
from typing import Callable, Dict, List, Optional

import aiohttp
import qrcode
from telegram import (
    BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
    ReplyKeyboardMarkup, Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src import tg_md, vless
from src.config import (
    ADMIN_IDS, AUTO_PUBLISH, GITHUB_REPO, GITHUB_TOKEN, HTTP_TEST_ROUNDS,
    INDEX_FILE, IRAN_FILE, MANUAL_FILE, PUBLISH_COUNT, PUBLISH_INTERVAL_MIN,
    RAW_BASE, STATS_FILE, SUB_B64_URL, SUB_IRAN_URL, SUB_URL,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TG_SAFE_MSG_LEN, VALID_FILE,
)
from src.logger import get_logger
from src.publisher import renderer
from src.publisher.publisher import Publisher

logger = get_logger("bot")

BOT_ENABLED = True

# آدرس فایل‌ها برای *خواندن*؛ SUB_* برای نمایش به کاربر است و ممکن است
# بعداً به دامنه‌ی دیگری اشاره کند.
VALID_URL = f"{RAW_BASE}/{VALID_FILE}" if RAW_BASE else ""
IRAN_URL = f"{RAW_BASE}/{IRAN_FILE}" if RAW_BASE else ""
STATS_URL = f"{RAW_BASE}/{STATS_FILE}" if RAW_BASE else ""
INDEX_URL = f"{RAW_BASE}/{INDEX_FILE}" if RAW_BASE else ""

CACHE_TTL = 300          # ۵ دقیقه — هم‌اندازه‌ی cache خود raw.githubusercontent
LIST_LIMIT = 10

_cache: Dict[str, object] = {"configs": [], "iran": [], "stats": {}, "index": {}}
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
    """آپدیت cache از GitHub — چهار فایل با هم.

    _last_fetch فقط در صورت موفقیت جلو می‌رود تا یک قطعی موقت شبکه، cache
    خالی را برای ۵ دقیقه قفل نکند.
    """
    global _last_fetch

    if not force and _cache["configs"] and time.time() - _last_fetch < CACHE_TTL:
        return

    valid_text, iran_text, stats_text, index_text = await asyncio.gather(
        fetch_from_github(VALID_URL),
        fetch_from_github(IRAN_URL),
        fetch_from_github(STATS_URL),
        fetch_from_github(INDEX_URL),
    )

    ok = False
    if valid_text:
        _cache["configs"] = _lines(valid_text)
        ok = True
    if iran_text:
        _cache["iran"] = _lines(iran_text)
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
            f"{len(_cache['iran'])} تأییدشده‌ی ایران"
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
# درخواست کاربر: دکمه‌ها همان‌جا که کیبورد است بمانند. is_persistent یعنی
# کیبورد بعد از هر پیام بسته نمی‌شود؛ فقط یک بار در /start فرستادنش کافی است.

BTN_BEST = "⭐ بهترین"
BTN_IRAN = "🇮🇷 مخصوص ایران"
BTN_RANDOM = "🎲 رندوم"
BTN_LIST = "📋 لیست"
BTN_SUB = "🔗 اشتراک"
BTN_STATS = "📊 آمار"
BTN_QR = "📷 QR"
BTN_HELP = "❓ راهنما"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_BEST), KeyboardButton(BTN_IRAN)],
        [KeyboardButton(BTN_RANDOM), KeyboardButton(BTN_LIST)],
        [KeyboardButton(BTN_SUB), KeyboardButton(BTN_STATS)],
        [KeyboardButton(BTN_QR), KeyboardButton(BTN_HELP)],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="یک دکمه را بزن یا /help",
)
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
            InlineKeyboardButton("📷 QR", callback_data=f"qr_{index}"),
            InlineKeyboardButton("▶️ بعدی", callback_data=f"get_next_{index + 1}"),
        ],
        [InlineKeyboardButton("🔗 لینک اشتراک", callback_data="sub")],
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
        f"👇 از دکمه‌های پایین استفاده کن — /help برای همه‌ی دستورها."
    )
    await reply(update, text, reply_markup=MAIN_KEYBOARD)
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
            InlineKeyboardButton("🎲 یکی دیگه", callback_data="random"),
            InlineKeyboardButton("📷 QR", callback_data=f"qr_{index}"),
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
            [[InlineKeyboardButton("📷 QR لینک", callback_data="qr_sub")]]
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
        f"  ۴ دسترسی از ایران: {layer('layer4_iran', 'passed')}\n"
        f"  ۵ TLS: {layer('layer5_tls', 'passed')}\n"
        f"  ۶ Geo: {layer('layer6_geo', 'passed')}\n"
        f"  ۷ HTTP واقعی ({rounds} دور): {layer('layer7_http', 'passed')}\n"
        + (f"  قیف: {tg_md.code(' → '.join(str(n) for n in funnel))}\n" if funnel else "")
        + f"\n📡 *امنیت:* 🔐 {labels.count('Reality')} | "
        f"🔒 {labels.count('TLS')} | ☁️ {labels.count('Other')}\n"
        f"🌍 *کشورها:* {country_text or 'نامشخص'}"
    )
    await reply(update, text)
@user_command
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *راهنمای کامل*\n\n"
        "🔹 /get — بهترین کانفیگ\n"
        "🔹 /iran — فقط تأییدشده‌های داخل ایران 🇮🇷\n"
        "🔹 /list — لیست همه‌ی کانفیگ‌ها\n"
        "🔹 /reality — فقط Reality\n"
        "🔹 /tls — فقط TLS\n"
        "🔹 /random — یک کانفیگ رندوم\n"
        "🔹 /qr [شماره] — QR code کانفیگ\n"
        "🔹 /ping — تأخیر کانفیگ‌ها\n"
        "🔹 /sub — لینک اشتراک\n"
        "🔹 /stats — آمار کامل pipeline\n\n"
        "💡 *کانفیگ‌ها از ۷ لایه رد شده‌اند:*\n"
        "فرمت → حذف تکراری → TCP → *دسترسی از ایران* → TLS → Geo → HTTP واقعی\n\n"
        "🇮🇷 لایه‌ی «دسترسی از ایران» با نودهای ایرانی check-host تست می‌شود؛ "
        "بقیه‌ی لایه‌ها از سرور آمریکا اجرا می‌شوند و فقط سالم بودن سرور را "
        "می‌سنجند. کانفیگ‌های /iran بالاترین شانس باز شدن را دارند.\n\n"
        f"🔁 کانال هر {PUBLISH_INTERVAL_MIN} دقیقه {PUBLISH_COUNT} کانفیگ تازه "
        "می‌گذارد و پیام آخر هر دسته لینک اشتراک است."
    )
    await reply(update, text, reply_markup=MAIN_KEYBOARD)


# ─── دکمه‌های ثابت → همان هندلرها ──────────────────────────

BUTTON_ROUTES: Dict[str, Callable] = {
    BTN_BEST: cmd_get,
    BTN_IRAN: cmd_iran,
    BTN_RANDOM: cmd_random,
    BTN_LIST: cmd_list,
    BTN_SUB: cmd_sub,
    BTN_STATS: cmd_stats,
    BTN_QR: cmd_qr,
    BTN_HELP: cmd_help,
}


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """متن دکمه‌های ثابت را به دستور معادلش می‌رساند.

    دکمه‌ی ReplyKeyboard یک پیام متنی معمولی می‌فرستد (نه callback)، پس
    مسیردهی روی متن انجام می‌شود.
    """
    message = update.effective_message
    text = (message.text or "").strip() if message else ""
    handler = BUTTON_ROUTES.get(text)
    if handler is not None:
        await handler(update, context)
        return
    if text.lower().startswith("vless://"):
        await reply(update, "🧪 برای تست یک کانفیگ: `/test <لینک>` (فقط ادمین)")
        return
    await reply(update, "👇 از دکمه‌های پایین استفاده کن یا /help را بزن.",
                reply_markup=MAIN_KEYBOARD)
# ─── انتشار در کانال ──────────────────────────────────────

async def _publish_once(bot, trigger: str = "auto") -> Dict:
    """یک دوره‌ی انتشار: ۱۰ کارت + پیام لینک اشتراک.

    هم حلقه‌ی خودکار و هم /publish از همین می‌گذرند تا رفتارشان یکی بماند
    (چرخش، cooldown و ثبت وضعیت همه در publisher انجام می‌شود).
    """
    empty: Dict = {"selected": 0, "sent": 0, "failed": 0, "ids": []}
    await refresh_cache(force=True)
    configs = load_configs()
    if not configs:
        logger.warning("⚠️ دوره‌ی انتشار رد شد — پول کانفیگ خالی است")
        return {**empty, "trigger": trigger, "at": _now_utc()}

    result = await Publisher(bot=bot).publish_batch(configs, load_stats())
    result["trigger"] = trigger
    result["at"] = _now_utc()
    _publish_log.append(dict(result))
    del _publish_log[:-5]
    return result


def _now_utc() -> str:
    return time.strftime("%H:%M UTC", time.gmtime())


async def auto_publish_loop(app: Application) -> None:
    """حلقه‌ی انتشار — عمداً asyncio خام.

    APScheduler نصب نیست (و JobQueue تلگرام به آن نیاز دارد)؛ این حلقه هیچ
    وابستگی‌ای اضافه نمی‌کند. یک استثنا در یک دوره، حلقه را نمی‌کشد.
    """
    interval = max(1, PUBLISH_INTERVAL_MIN) * 60
    await asyncio.sleep(20)   # تا اتصال ربات و اولین cache کامل شود
    while True:
        try:
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
    await reply(
        update,
        f"{'✅' if result['sent'] else '⚠️'} انتشار: "
        f"{result['selected']} کانفیگ انتخاب شد | "
        f"{result['sent']} پیام موفق | {result['failed']} ناموفق",
    )
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
        f"{'🟢 هر ' + str(PUBLISH_INTERVAL_MIN) + ' دقیقه' if AUTO_PUBLISH else '🔴 خاموش'}",
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
@admin_only
async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ENABLED
    BOT_ENABLED = True
    await reply(update, "🟢 ربات روشن شد!")


@admin_only
async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ENABLED
    BOT_ENABLED = False
    await reply(update, "🔴 ربات خاموش شد!")


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
@admin_only
async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تست یک کانفیگ دلخواه: فرمت → TCP → TLS → دسترسی از ایران."""
    if not context.args:
        await reply(update, "❌ کانفیگ رو بعد از /test بفرست.")
        return
    cfg = " ".join(context.args).strip()
    await reply(update, "🔄 در حال تست...")

    from src.tester.checkhost_tester import check_endpoint, endpoint_of
    from src.tester.format_validator import validate_vless
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
                InlineKeyboardButton("🎲 یکی دیگه", callback_data="random"),
                InlineKeyboardButton("📷 QR", callback_data=f"qr_{index}"),
            ]]),
        )

    elif data == "iran":
        await cmd_iran(update, context)
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
    ("list", "لیست کانفیگ‌ها", cmd_list),
    ("reality", "کانفیگ‌های Reality", cmd_reality),
    ("tls", "کانفیگ‌های TLS", cmd_tls),
    ("random", "کانفیگ رندوم", cmd_random),
    ("qr", "QR code کانفیگ", cmd_qr),
    ("ping", "تأخیر کانفیگ‌ها", cmd_ping),
    ("sub", "لینک اشتراک", cmd_sub),
    ("stats", "آمار pipeline", cmd_stats),
    ("help", "راهنما", cmd_help),
]

ADMIN_COMMANDS = [
    ("run", cmd_run),
    ("publish", cmd_publish),
    ("status", cmd_status),
    ("on", cmd_on),
    ("off", cmd_off),
    ("add", cmd_add),
    ("test", cmd_test),
]
async def setup_commands(app: Application) -> None:
    """منوی دستورها. خطا نباید جلوی بالا آمدن ربات را بگیرد."""
    try:
        await app.bot.set_my_commands(
            [BotCommand(name, desc) for name, desc, _ in USER_COMMANDS]
        )
        logger.info("✅ منوی دستورها ثبت شد")
    except Exception as exc:
        logger.warning(f"⚠️ ثبت منوی دستورها ناموفق: {exc}")


async def post_init(app: Application) -> None:
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
    for name, handler in ADMIN_COMMANDS:
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
