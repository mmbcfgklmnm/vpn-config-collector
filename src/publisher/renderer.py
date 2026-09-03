"""ساخت «کارت مشخصات» یک کانفیگ — یک جا برای کانال و ربات.

چرا جدا از publisher: کاربر خواست هر کانفیگ در پیام *مستقل* خودش با
مشخصات کامل پست شود. همان کارت در ربات هم استفاده می‌شود (دستور /get و
دکمه‌های شیشه‌ای)، و دو نسخه‌ی جدا از این متن به‌سرعت از هم واگرا می‌شد.

همه‌ی مقدارهای پویا از src.tg_md رد می‌شوند: اسم کانفیگ از منابع عمومی
scrape شده و یک `_` در آن با ParseMode.MARKDOWN خطای ۴۰۰ می‌دهد، یعنی
پیام هیچ‌وقت فرستاده نمی‌شود.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import cdn, tg_md, vless
from src.clean_ip import REVIVE_MARK
from src.config import CHANNEL_USERNAME

# پرچم از کد دوحرفی: هر حرف → regional indicator symbol.
_FLAG_BASE = 0x1F1E6


def flag(country: str) -> str:
    code = (country or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return "🏳"
    return "".join(chr(_FLAG_BASE + ord(ch) - ord("A")) for ch in code)


# نام فارسی کشورهای پرتکرار در پول. بقیه با کد دوحرفی نمایش داده می‌شوند.
COUNTRY_FA = {
    "DE": "آلمان", "NL": "هلند", "US": "آمریکا", "GB": "انگلیس",
    "FR": "فرانسه", "FI": "فنلاند", "SE": "سوئد", "PL": "لهستان",
    "TR": "ترکیه", "AE": "امارات", "RU": "روسیه", "CA": "کانادا",
    "AT": "اتریش", "CH": "سوئیس", "IT": "ایتالیا", "ES": "اسپانیا",
    "LT": "لیتوانی", "LV": "لتونی", "EE": "استونی", "CZ": "چک",
    "RO": "رومانی", "BG": "بلغارستان", "HU": "مجارستان", "UA": "اوکراین",
    "SG": "سنگاپور", "JP": "ژاپن", "KR": "کره جنوبی", "HK": "هنگ‌کنگ",
    "IN": "هند", "AU": "استرالیا", "BR": "برزیل", "AM": "ارمنستان",
    "GE": "گرجستان", "AZ": "آذربایجان", "KZ": "قزاقستان", "CY": "قبرس",
    "IE": "ایرلند", "NO": "نروژ", "DK": "دانمارک", "BE": "بلژیک",
    "LU": "لوکزامبورگ", "MD": "مولداوی", "RS": "صربستان", "IL": "اسرائیل",
}

SECURITY_LABEL = {
    "reality": "🔐 Reality",
    "tls": "🔒 TLS",
    "xtls": "🔒 XTLS",
    "none": "☁️ CDN (بدون TLS)",
}

NETWORK_LABEL = {
    "ws": "WebSocket",
    "httpupgrade": "HTTPUpgrade",
    "xhttp": "XHTTP",
    "grpc": "gRPC",
    "h2": "HTTP/2",
    "http": "HTTP/2",
    "tcp": "TCP",
    "raw": "TCP",
    "kcp": "mKCP",
    "quic": "QUIC",
}

CDN_LABEL = {
    "cloudflare": "☁️ Cloudflare",
    "cdn": "☁️ CDN",
    "domain": "🌐 دامنه",
    "ip": "🖥 IP مستقیم",
}

# کیفیت بر اساس تأخیر واقعیِ تونل (اندازه‌گیری xray)، نه پینگ TCP.
_QUALITY = ((300, "🟢 عالی"), (700, "🟡 خوب"), (1500, "🟠 متوسط"))

# برچسب منبع کارت. لازم است چون سهمیه‌ی ۱۰تایی از سه جا پر می‌شود و ادعای
# «تست‌شده» برای هر سه‌تا درست نیست: پول ذخیره تونلش تست نشده و اهدایی از
# کاربر آمده. کاربر باید تفاوت را ببیند، نه اینکه حدس بزند.
BADGE = {
    "pool": "🗃 *تست‌نشده* — لایه‌های TCP/دسترسی/TLS را پاس کرده، تونل امتحان نشده",
    "donated": "🎁 *اهدایی یکی از کاربران* — اعتبارسنجی فرمت شده، تونل امتحان نشده",
}


def quality(latency_ms: float) -> str:
    if not latency_ms or latency_ms <= 0:
        return "⚪ نامعلوم"
    for limit, label in _QUALITY:
        if latency_ms < limit:
            return label
    return "🔴 کند"


def stability_text(loss_pct: float, jitter_ms: float = -1.0) -> str:
    """جمله‌ی پایداری از افت بسته و لرزش.

    ‏-1 یعنی «اندازه‌گیری نشد» و در آن حالت رشته‌ی خالی برمی‌گردد: کارت هیچ
    ادعایی نمی‌کند، نه «پایدار» و نه «ناپایدار». همان قاعده‌ی همیشگی —
    «تست نشد» ≠ «رد شد» — و برعکسش هم درست است: تست‌نشده «سالم» نیست.

    آستانه‌ها به خواسته‌ی کاربر افت‌محورند نه پینگ‌محور: «نودی با پینگ ۱۰۰ms
    و ۰٪ افت از نودی با پینگ ۵۰ms و ۲۰٪ افت بسیار ارزشمندتر است.»
    """
    if loss_pct is None or loss_pct < 0:
        return ""
    if loss_pct == 0:
        label = "🟢 پایدار — بدون افت بسته"
    elif loss_pct <= 5:
        label = f"🟢 پایدار — افت {round(loss_pct)}%"
    elif loss_pct <= 15:
        label = f"🟡 افت جزئی — {round(loss_pct)}%"
    else:
        label = f"🔴 ناپایدار — افت {round(loss_pct)}%"
    if jitter_ms is not None and jitter_ms >= 0:
        label += f"  •  لرزش {round(jitter_ms)}ms"
    return label


def speed_text(kbps: float) -> str:
    """سرعت خوانا؛ ۰ یعنی اندازه‌گیری نشد و چیزی چاپ نمی‌شود."""
    if not kbps or kbps <= 0:
        return ""
    if kbps >= 1024:
        return f"{kbps / 1024:.1f} MB/s"
    return f"{round(kbps)} KB/s"


def country_text(country: str) -> str:
    code = (country or "").strip().upper()
    if not code or code == "??":
        return "🏳 نامعلوم"
    name = COUNTRY_FA.get(code)
    return f"{flag(code)} {name} ({code})" if name else f"{flag(code)} {code}"


def describe(config: str) -> Dict[str, object]:
    """همه‌ی مشخصاتی که هم کانال و هم ربات لازم دارند — یک بار پارس."""
    info = vless.parse(config)
    country = vless.get_country(config)
    latency = vless.get_latency_ms(config)
    iran_ms = vless.get_iran_ms(config)
    host = info.host if info else ""
    port = info.port if info else 0
    network = info.network if info else ""
    security = info.security if info else ""
    return {
        "id": vless.short_id(config),
        "name": vless.get_name(config, 40) or "بدون نام",
        "host": host,
        "port": port,
        "sni": (info.sni if info else "") or "",
        "network": network,
        "security": security,
        "country": country,
        "latency_ms": 0.0 if latency == float("inf") else latency,
        "iran_ms": iran_ms,
        "loss_pct": vless.get_loss_pct(config),
        "jitter_ms": vless.get_jitter_ms(config),
        "speed_kbps": vless.get_speed_kbps(config),
        # ورودی احیاشده: آدرس یک IP تمیز CF است و Host/SNI مسیر اصلی را
        # نگه داشته. کاربر باید بداند، وگرنه «IP مستقیم» بودنِ آدرس گیج‌کننده است.
        "revived": REVIVE_MARK in vless.split_fragment(config)[1],
        "cdn": cdn.classify(host, port) if host else "ip",
        "is_cdn": bool(host) and cdn.classify(host, port) in ("cloudflare", "cdn"),
    }


def spec_lines(config: str, spec: Optional[Dict] = None) -> List[str]:
    """خطوط مشخصات بدون خود لینک — برای ترکیب در قالب‌های مختلف."""
    spec = spec or describe(config)
    net = str(spec["network"])
    lines = [
        f"🌍 کشور: {country_text(str(spec['country']))}",
        f"🛡 امنیت: {SECURITY_LABEL.get(str(spec['security']), '🔑 ' + (str(spec['security']) or 'نامعلوم'))}",
        f"🚀 ترنسپورت: {NETWORK_LABEL.get(net, net or 'نامعلوم')}"
        + ("  •  " + CDN_LABEL[str(spec["cdn"])] if spec["cdn"] in CDN_LABEL else ""),
        f"🔌 پورت: {spec['port']}",
    ]
    sni = str(spec["sni"])
    if sni and sni != str(spec["host"]):
        lines.append(f"🏷 SNI: {tg_md.strip_md(sni, 40)}")
    latency = float(spec["latency_ms"] or 0)
    if latency > 0:
        lines.append(f"⚡️ تأخیر تونل: {round(latency)}ms  •  {quality(latency)}")
    stability = stability_text(
        float(spec.get("loss_pct", -1.0)), float(spec.get("jitter_ms", -1.0))
    )
    if stability:
        lines.append(f"📶 پایداری: {stability}")
    speed = speed_text(float(spec.get("speed_kbps") or 0))
    if speed:
        lines.append(f"⬇️ سرعت دانلود: {speed}")
    iran_ms = float(spec["iran_ms"] or 0)
    if iran_ms > 0:
        lines.append(f"🇮🇷 دسترسی از ایران: ✅ تأییدشده ({round(iran_ms)}ms)")
    if spec.get("revived"):
        lines.append("♻️ ورودی احیاشده با IP تمیز کلودفلر (Host/SNI اصلی حفظ شده)")
    return lines


def spec_card(
    config: str,
    index: int = 0,
    total: int = 0,
    verified_rounds: int = 0,
    badge: str = "",
) -> str:
    """کارت کامل یک کانفیگ: تیتر + مشخصات + لینک قابل کپی.

    `badge` منبع کانفیگ است ("pool" یا "donated"). وقتی ست باشد، ادعای «در N
    دور تست شد» *چاپ نمی‌شود* — آن جمله فقط برای کانفیگی درست است که لایه ۷
    واقعاً تأییدش کرده.
    """
    spec = describe(config)
    head = f"🔹 *کانفیگ {index}/{total}*" if index and total else "🔹 *کانفیگ*"
    lines = [
        f"{head}   `#{spec['id']}`",
        f"📛 {tg_md.strip_md(spec['name'], 40)}",
    ]
    note = BADGE.get(badge, "")
    if note:
        lines.append(note)
    lines += ["", *spec_lines(config, spec)]
    if verified_rounds > 1 and not note:
        lines.append(f"✅ در {verified_rounds} دور پشت سر هم تست شد")
    lines += ["", tg_md.code(config)]
    if CHANNEL_USERNAME:
        lines += ["", f"@{CHANNEL_USERNAME}"]
    return "\n".join(lines)


def one_line(config: str) -> str:
    """خلاصه‌ی تک‌خطی برای فهرست‌ها (ربات، /list)."""
    spec = describe(config)
    bits = [f"`#{spec['id']}`", flag(str(spec["country"]))]
    label = SECURITY_LABEL.get(str(spec["security"]), "🔑")
    bits.append(label.split(" ", 1)[-1])
    latency = float(spec["latency_ms"] or 0)
    if latency > 0:
        bits.append(f"{round(latency)}ms")
    loss = float(spec.get("loss_pct", -1.0))
    if loss == 0:
        bits.append("🟢")                      # پایدارِ اندازه‌گیری‌شده
    elif loss > 0:
        bits.append(f"P{round(loss)}%")
    speed = speed_text(float(spec.get("speed_kbps") or 0))
    if speed:
        bits.append(speed)
    if float(spec["iran_ms"] or 0) > 0:
        bits.append("🇮🇷")
    if spec.get("revived"):
        bits.append("♻️")
    return " • ".join(bits) + f" — {tg_md.strip_md(spec['name'], 24)}"
