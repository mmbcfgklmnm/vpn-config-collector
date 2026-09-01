"""
VPN Config Bot v2 — ربات تلگرام.

اصلاحات این نسخه:
  ۱. همه‌ی مقدارهای پویا (اسم کانفیگ، اسم کاربر، متن خطا) از src.tg_md رد
     میشن. قبلاً اسم scrape‌شده با `_` یا `*` باعث خطای 400 تلگرام
     («Can't parse entities») می‌شد و پیام هیچ‌وقت به کاربر نمی‌رسید.
  ۲. refresh_cache در همه‌ی دستورها و در callback ها اجرا میشه — قبلاً فقط
     در /start بود، پس بقیه‌ی دستورها لیست کهنه نشان می‌دادند.
  ۳. _last_fetch فقط وقتی موفق باشیم آپدیت میشه؛ قبلاً یک قطعی شبکه cache
     خالی رو ۵ دقیقه قفل می‌کرد.
  ۴. /get واقعاً بر اساس تأخیر مرتب میشه (قبلاً فقط Reality رو جلو می‌آورد).
  ۵. دکمه‌ی QR شماره‌ی کانفیگ واقعی رو می‌بره؛ قبلاً همیشه qr_0 بود.
  ۶. /add در configs/manual.txt نوشته میشه نه valid.txt — قبلاً کل خروجی
     pipeline با محتوای cache بازنویسی می‌شد.
  ۷. تشخیص Reality/TLS از پارامتر security می‌آید نه جست‌وجوی متن در لینک.
"""
import functools
import io
import json
import os
import random
import sys
import time
from typing import Callable, List, Optional

import aiohttp
import qrcode
from telegram import (
    BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src import tg_md, vless
from src.config import (
    TELEGRAM_BOT_TOKEN, VALID_FILE, STATS_FILE, ADMIN_IDS,
    SUB_URL, GITHUB_TOKEN, GITHUB_REPO, MANUAL_FILE, TG_SAFE_MSG_LEN,
)
from src.logger import get_logger

logger = get_logger("bot")

BOT_ENABLED = True

# GITHUB_REPO از config می‌آید (قبلاً os.getenv دوباره خوانده می‌شد و اگر
# فقط REPO_URL تنظیم بود این URL به /main/... بی‌نام تبدیل می‌شد).
GITHUB_RAW_BASE = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/main" if GITHUB_REPO else ""
)
VALID_URL = f"{GITHUB_RAW_BASE}/configs/valid.txt" if GITHUB_RAW_BASE else ""
STATS_URL = f"{GITHUB_RAW_BASE}/configs/stats.json" if GITHUB_RAW_BASE else ""

# cache محلی
_configs_cache: List[str] = []
_stats_cache: dict = {}
_last_fetch: float = 0.0
CACHE_TTL = 300  # ۵ دقیقه

LIST_LIMIT = 10


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
    """آپدیت cache از GitHub.

    _last_fetch فقط در صورت موفقیت جلو می‌ره تا یک قطعی موقت شبکه،
    cache خالی رو برای ۵ دقیقه قفل نکنه.
    """
    global _configs_cache, _stats_cache, _last_fetch

    if not force and _configs_cache and time.time() - _last_fetch < CACHE_TTL:
        return

    ok = False

    text = await fetch_from_github(VALID_URL)
    if text:
        _configs_cache = [line.strip() for line in text.splitlines() if line.strip()]
        ok = True

    stats_text = await fetch_from_github(STATS_URL)
    if stats_text:
        try:
            _stats_cache = json.loads(stats_text)
            ok = True
        except ValueError:
            logger.debug("stats.json خراب بود")

    if ok:
        _last_fetch = time.time()
        logger.info(f"🔄 cache آپدیت: {len(_configs_cache)} کانفیگ")


# ─── کمکی ─────────────────────────────────────────────────

def load_configs() -> List[str]:
    """فایل محلی اگه محتوا داشته باشه، وگرنه cache گیت‌هاب."""
    try:
        with open(VALID_FILE, encoding="utf-8") as fh:
            local = [line.strip() for line in fh if line.strip()]
        if local:
            return local
    except OSError:
        pass
    return list(_configs_cache)


def load_stats() -> dict:
    try:
        with open(STATS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return dict(_stats_cache)


def is_admin(uid: Optional[int]) -> bool:
    return uid is not None and uid in ADMIN_IDS


def sort_best(configs: List[str]) -> List[str]:
    """کم‌تأخیرترین اول؛ در تأخیر برابر، Reality جلوتر."""
    return sorted(
        configs,
        key=lambda c: (vless.get_latency_ms(c), 0 if vless.is_reality(c) else 1),
    )


def icon_for(config: str) -> str:
    return {"Reality": "🔐", "TLS": "🔒"}.get(vless.get_security_label(config), "🔑")

def label(config: str) -> str:
    """خط توصیف یک کانفیگ، امن برای Markdown."""
    country = vless.get_country(config)
    latency = vless.get_latency(config)
    name = tg_md.strip_md(vless.get_name(config), 30) or "بدون نام"
    flag = f"[{country}] " if country else ""
    tail = f" • {latency}" if latency else ""
    return f"{icon_for(config)} {flag}{name}{tail}"


def make_qr(text: str) -> io.BytesIO:
    qr = qrcode.QRCode(box_size=8, border=3)
    qr.add_data(text)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    buf.seek(0)
    return buf


async def reply(update: Update, text: str, **kwargs) -> None:
    """پاسخ امن: پیام ممکنه edit شده یا از callback آمده باشه."""
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        tg_md.truncate(text, TG_SAFE_MSG_LEN),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
        **kwargs,
    )


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


def format_config_list(configs: List[str], title: str, max_n: int = LIST_LIMIT) -> str:
    if not configs:
        return f"❌ کانفیگ {title} موجود نیست."
    lines = [f"🔐 *{title}* — {len(configs)} کانفیگ\n"]
    for cfg in configs[:max_n]:
        lines.append(f"{label(cfg)}\n{tg_md.code(cfg)}\n")
    if len(configs) > max_n:
        lines.append(f"\n_... و {len(configs) - max_n} کانفیگ دیگه_")
    return "\n".join(lines)


def config_card(config: str, index: int, title: str) -> str:
    security = vless.get_security_label(config)
    country = vless.get_country(config)
    latency = vless.get_latency(config) or "نامشخص"
    name = tg_md.strip_md(vless.get_name(config), 30) or "بدون نام"
    flag = f"[{country}] " if country else ""
    return (
        f"{icon_for(config)} *{title}*\n"
        f"{'─' * 25}\n"
        f"📌 نام: {flag}{name}\n"
        f"🔑 نوع: {security}\n"
        f"⚡ تأخیر: {latency}\n\n"
        f"{tg_md.code(config)}"
    )


def card_keyboard(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📷 QR Code", callback_data=f"qr_{index}"),
        InlineKeyboardButton("▶️ بعدی", callback_data=f"get_next_{index + 1}"),
    ]])

# ─── دستورات کاربر ────────────────────────────────────────

@user_command
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    configs = load_configs()
    stats = load_stats()
    ts = str(stats.get("timestamp", ""))[:16].replace("T", " ")
    user = update.effective_user
    name = tg_md.strip_md(user.first_name if user else "", 30) or "دوست من"

    text = (
        f"👋 سلام *{name}*!\n\n"
        f"🛡️ *VPN Config Bot*\n"
        f"کانفیگ‌های VLESS تست‌شده و معتبر\n\n"
        f"📊 *وضعیت:*\n"
        f"  ✅ {len(configs)} کانفیگ معتبر\n"
        f"  🕐 آخرین آپدیت: {ts or 'نامشخص'}\n\n"
        f"📋 *دستورات:*\n"
        f"  /get — بهترین کانفیگ\n"
        f"  /list — لیست همه\n"
        f"  /reality — فقط Reality\n"
        f"  /tls — فقط TLS\n"
        f"  /random — رندوم\n"
        f"  /qr — QR code\n"
        f"  /ping — تأخیر کانفیگ‌ها\n"
        f"  /sub — لینک subscription\n"
        f"  /stats — آمار کامل\n"
        f"  /help — راهنما"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⭐ بهترین", callback_data="get"),
            InlineKeyboardButton("🎲 رندوم", callback_data="random"),
        ],
        [
            InlineKeyboardButton("🔐 Reality", callback_data="reality"),
            InlineKeyboardButton("🔒 TLS", callback_data="tls"),
        ],
        [
            InlineKeyboardButton("🔗 Subscription", callback_data="sub"),
            InlineKeyboardButton("📊 آمار", callback_data="stats"),
        ],
        [
            InlineKeyboardButton("📷 QR Code", callback_data="qr_0"),
            InlineKeyboardButton("🔄 آپدیت", callback_data="refresh"),
        ],
    ])
    await reply(update, text, reply_markup=keyboard)

@user_command
async def cmd_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بهترین کانفیگ بر اساس تأخیر."""
    configs = sort_best(load_configs())
    if not configs:
        await reply(update, "❌ هنوز کانفیگی موجود نیست.")
        return
    # ایندکس همان ترتیبی است که دکمه‌ها استفاده می‌کنند (sort_best).
    await reply(update, config_card(configs[0], 0, "بهترین کانفیگ"),
                reply_markup=card_keyboard(0))


@user_command
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, format_config_list(sort_best(load_configs()), "همه کانفیگ‌ها"))


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
        config_card(configs[index], index, "کانفیگ رندوم"),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎲 دیگه‌ای", callback_data="random"),
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
    lines = ["⚡ *تأخیر کانفیگ‌ها* _(اندازه‌گیری آخرین اجرا)_\n"]
    for i, cfg in enumerate(configs[:LIST_LIMIT], 1):
        lines.append(f"{i}. {label(cfg)}")
    await reply(update, "\n".join(lines))

@user_command
async def cmd_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    configs = load_configs()
    if not SUB_URL:
        await reply(update, "⚠️ لینک subscription تنظیم نشده (REPO_URL خالیه).")
        return
    text = (
        f"🔗 *Subscription Link*\n\n"
        f"{tg_md.code(SUB_URL)}\n\n"
        f"📊 {len(configs)} کانفیگ معتبر\n\n"
        f"📱 *نحوه استفاده:*\n"
        f"• *v2rayNG:* ➕ → از URL\n"
        f"• *Hiddify:* پروفایل جدید\n"
        f"• *NekoRay:* Profiles → New"
    )
    await reply(update, text, reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton("📷 QR لینک", callback_data="qr_sub")]]
    ))


@user_command
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = load_stats()
    configs = load_configs()
    pipe = stats.get("pipeline", {})

    labels = [vless.get_security_label(c) for c in configs]
    countries: dict = {}
    for cfg in configs:
        country = vless.get_country(cfg)
        if country:
            countries[country] = countries.get(country, 0) + 1
    country_text = " | ".join(
        f"{k}:{v}" for k, v in sorted(countries.items(), key=lambda x: -x[1])[:5]
    )

    def layer(name: str, key: str) -> str:
        return str(pipe.get(name, {}).get(key, "—"))

    text = (
        f"📊 *آمار کامل*\n"
        f"{'─' * 28}\n"
        f"🕐 آخرین آپدیت: {tg_md.code(str(stats.get('timestamp', '—'))[:19].replace('T', ' '))}\n"
        f"⏱️ مدت اجرا: {tg_md.code(str(stats.get('duration_seconds', '—')) + 's')}\n\n"
        f"📥 جمع‌آوری: {tg_md.code(stats.get('raw_collected', 0))}\n"
        f"✅ معتبر نهایی: *{stats.get('valid_configs', len(configs))}*\n\n"
        f"📡 *پروتکل:*\n"
        f"  🔐 Reality: {labels.count('Reality')}\n"
        f"  🔒 TLS: {labels.count('TLS')}\n\n"
        f"🌍 *کشورها:*\n  {country_text or 'نامشخص'}\n\n"
        f"🔬 *Pipeline:*\n"
        f"  لایه ۱ فرمت: {layer('layer1_format', 'valid')}\n"
        f"  لایه ۲ dedup: {layer('layer2_dedup', 'unique')}\n"
        f"  لایه ۳ TCP: {layer('layer3_tcp', 'connected')}\n"
        f"  لایه ۴ TLS: {layer('layer4_tls', 'passed')}\n"
        f"  لایه ۵ Geo: {layer('layer5_geo', 'passed')}\n"
        f"  لایه ۶ HTTP: {layer('layer6_http', 'passed')}"
    )
    await reply(update, text)

@user_command
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *راهنمای کامل*\n\n"
        "🔹 /get — بهترین کانفیگ بر اساس تأخیر\n"
        "🔹 /list — لیست همه کانفیگ‌ها\n"
        "🔹 /reality — فقط کانفیگ‌های Reality\n"
        "🔹 /tls — فقط کانفیگ‌های TLS\n"
        "🔹 /random — یه کانفیگ رندوم\n"
        "🔹 /qr [شماره] — QR code کانفیگ\n"
        "🔹 /ping — تأخیر کانفیگ‌ها\n"
        "🔹 /sub — لینک subscription\n"
        "🔹 /stats — آمار کامل pipeline\n\n"
        "💡 *نکته:* کانفیگ‌ها از ۶ لایه تست رد شدن:\n"
        "فرمت → Dedup → TCP → TLS → Geo → HTTP"
    )
    await reply(update, text)


# ─── دستورات ادمین ────────────────────────────────────────

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
    stats = load_stats()
    age = int(time.time() - _last_fetch) if _last_fetch else -1
    text = (
        f"🖥️ *وضعیت سیستم*\n\n"
        f"{'🟢 روشن' if BOT_ENABLED else '🔴 خاموش'}\n"
        f"✅ {len(configs)} کانفیگ\n"
        f"🕐 {tg_md.code(str(stats.get('timestamp', '—'))[:19])}\n"
        f"🔄 cache: {age if age >= 0 else '—'}s پیش | {len(_configs_cache)} از گیت‌هاب\n"
        f"📁 valid.txt: {'✅' if os.path.exists(VALID_FILE) else '❌'}\n"
        f"📁 stats.json: {'✅' if os.path.exists(STATS_FILE) else '❌'}\n"
        f"📁 manual.txt: {'✅' if os.path.exists(MANUAL_FILE) else '❌'}"
    )
    await reply(update, text)


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

    نسخه‌ی قبلی valid.txt رو با محتوای cache بازنویسی می‌کرد، یعنی خروجی
    pipeline پاک می‌شد و اجرای بعدی گیت‌هاب هم اون رو دوباره overwrite
    می‌کرد؛ کانفیگ دستی هر بار گم می‌شد.
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
        existing: List[str] = []
        if os.path.exists(MANUAL_FILE):
            with open(MANUAL_FILE, encoding="utf-8") as fh:
                existing = [line.strip() for line in fh if line.strip()]
        if cfg in existing:
            await reply(update, "⚠️ این کانفیگ قبلاً در manual.txt هست.")
            return
        parent = os.path.dirname(MANUAL_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(MANUAL_FILE, "a", encoding="utf-8") as fh:
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
    """تست یک کانفیگ دلخواه: فرمت → TCP → TLS."""
    if not context.args:
        await reply(update, "❌ کانفیگ رو بعد از /test بفرست.")
        return
    cfg = " ".join(context.args).strip()
    await reply(update, "🔄 در حال تست...")

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

    await reply(update, "🧪 *نتیجه تست*\n\n" + "\n".join(lines))

# ─── Callback Queries ──────────────────────────────────────

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
            config_card(configs[index], index, f"کانفیگ #{index + 1}"),
            reply_markup=card_keyboard(index),
        )

    elif data == "random":
        if not configs:
            await reply(update, "❌ کانفیگی موجود نیست.")
            return
        index = random.randrange(len(configs))
        await reply(
            update,
            config_card(configs[index], index, "کانفیگ رندوم"),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎲 دیگه‌ای", callback_data="random"),
                InlineKeyboardButton("📷 QR", callback_data=f"qr_{index}"),
            ]]),
        )

    elif data == "reality":
        await reply(update, format_config_list(
            [c for c in configs if vless.is_reality(c)], "🔐 Reality"
        ))

    elif data == "tls":
        await reply(update, format_config_list(
            [c for c in configs if vless.get_security_label(c) == "TLS"], "🔒 TLS"
        ))

    elif data == "sub":
        if SUB_URL:
            await reply(
                update,
                f"🔗 *Subscription:*\n{tg_md.code(SUB_URL)}\n\n"
                f"📊 {len(configs)} کانفیگ",
            )
        else:
            await reply(update, "⚠️ لینک subscription تنظیم نشده.")

    elif data == "stats":
        stats = load_stats()
        await reply(
            update,
            f"📊 *آمار*\n"
            f"✅ {stats.get('valid_configs', len(configs))} کانفیگ\n"
            f"🕐 {tg_md.code(str(stats.get('timestamp', '—'))[:19])}",
        )

    elif data == "refresh":
        await reply(update, f"🔄 *آپدیت شد*\n✅ {len(configs)} کانفیگ موجود")

    elif data.startswith("qr_"):
        target = data[3:]
        if target == "sub":
            payload, caption = SUB_URL, "لینک subscription"
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

# ─── راه‌اندازی ────────────────────────────────────────────

USER_COMMANDS = [
    ("start", "شروع", cmd_start),
    ("get", "بهترین کانفیگ", cmd_get),
    ("list", "لیست کانفیگ‌ها", cmd_list),
    ("reality", "کانفیگ‌های Reality", cmd_reality),
    ("tls", "کانفیگ‌های TLS", cmd_tls),
    ("random", "کانفیگ رندوم", cmd_random),
    ("qr", "QR code کانفیگ", cmd_qr),
    ("ping", "تأخیر کانفیگ‌ها", cmd_ping),
    ("sub", "لینک subscription", cmd_sub),
    ("stats", "آمار pipeline", cmd_stats),
    ("help", "راهنما", cmd_help),
]

ADMIN_COMMANDS = [
    ("run", cmd_run),
    ("status", cmd_status),
    ("on", cmd_on),
    ("off", cmd_off),
    ("add", cmd_add),
    ("test", cmd_test),
]


async def setup_commands(app: Application) -> None:
    """منوی دستورها. خطا نباید جلوی بالا آمدن ربات رو بگیره."""
    try:
        await app.bot.set_my_commands(
            [BotCommand(name, desc) for name, desc, _ in USER_COMMANDS]
        )
        logger.info("✅ منوی دستورها ثبت شد")
    except Exception as exc:
        logger.warning(f"⚠️ ثبت منوی دستورها ناموفق: {exc}")


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN تنظیم نشده")
        raise SystemExit(1)
    if not ADMIN_IDS:
        logger.warning("⚠️ ADMIN_IDS خالیه — دستورهای ادمین برای هیچ‌کس فعال نیست")
    if not GITHUB_RAW_BASE:
        logger.warning("⚠️ GITHUB_REPO تنظیم نشده — cache گیت‌هاب غیرفعاله")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    for name, _desc, handler in USER_COMMANDS:
        app.add_handler(CommandHandler(name, handler))
    for name, handler in ADMIN_COMMANDS:
        app.add_handler(CommandHandler(name, handler))
    app.add_handler(CallbackQueryHandler(handle_callback))

    app.post_init = setup_commands

    logger.info("🤖 ربات شروع شد")
    # drop_pending_updates: پیام‌های زمان خاموشی دوباره پردازش نمیشن.
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()













