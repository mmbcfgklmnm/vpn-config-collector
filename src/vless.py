"""پارس و ساخت لینک‌های VLESS — یک جا برای همه‌ی ماژول‌ها.

قبلاً هر ماژول (format_validator, deduplicator, tls_tester, http_tester,
geo_checker, bot) کوئری رو دستی با split("&") و split("=") می‌شکست و
مقدارهای percent-encoded رو decode نمی‌کرد. نتیجه‌اش این بود که
`path=%2Fws` به xray به‌صورت literal `%2Fws` می‌رسید و همه‌ی کانفیگ‌های
WebSocket در لایه ۶ fail می‌شدند — به دلیل پارس، نه شبکه.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

SCHEME = "vless://"

_WHITESPACE = re.compile(r"\s+")
_NON_B64 = re.compile(r"[^A-Za-z0-9+/=]")


def split_fragment(config: str) -> Tuple[str, str]:
    """جدا کردن بخش اصلی از fragment (اسم) — روی اولین # جدا می‌کنیم.

    fragment خودش می‌تونه # داشته باشه، پس partition درسته و split("#")[0]
    هم همون نتیجه رو می‌ده؛ ولی برای گرفتن *اسم* باید از اولین # به بعد
    رو کامل برداشت، نه split("#")[-1] که وسط اسم رو می‌بره.
    """
    base, _, fragment = config.partition("#")
    return base, fragment


def parse_params(query: str) -> Dict[str, str]:
    """کوئری‌استرینگ → dict با کلید lowercase و مقدار percent-decoded.

    از parse_qs استفاده نمی‌کنیم چون `+` رو به space تبدیل می‌کنه و
    این برای مقدارهایی مثل path مخرب است.
    """
    params: Dict[str, str] = {}
    for part in query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        key = unquote(key).strip().lower()
        if key:
            params[key] = unquote(value)
    return params


@dataclass
class Vless:
    """نمای پارس‌شده‌ی یک لینک VLESS."""

    uuid: str = ""
    host: str = ""
    port: int = 0
    params: Dict[str, str] = field(default_factory=dict)
    name: str = ""
    raw: str = ""

    @property
    def security(self) -> str:
        return self.params.get("security", "none").lower()

    @property
    def network(self) -> str:
        return self.params.get("type", "tcp").lower()

    @property
    def sni(self) -> str:
        return self.params.get("sni") or self.params.get("peer") or self.host

    @property
    def is_reality(self) -> bool:
        return self.security == "reality"


def parse(config: str) -> Optional[Vless]:
    """پارس یک لینک VLESS. اگه ساختار پایه خراب بود None برمی‌گردونه."""
    config = config.strip()
    if not config.lower().startswith(SCHEME):
        return None
    base, fragment = split_fragment(config)
    try:
        parsed = urlparse(base)
        return Vless(
            uuid=(parsed.username or ""),
            host=(parsed.hostname or ""),
            port=(parsed.port or 0),
            params=parse_params(parsed.query),
            name=unquote(fragment),
            raw=config,
        )
    except ValueError:
        # urlparse روی پورت غیرعددی ValueError می‌ده
        return None


# ─── برچسب‌گذاری (کشور/تأخیر در fragment) ─────────────────────

def build_tag(latency_ms: float = 0, country: str = "") -> str:
    """ساخت بخش برچسب: مثلاً "NL|84ms"."""
    parts = []
    if country and country not in ("??", ""):
        parts.append(country)
    if latency_ms and latency_ms > 0:
        parts.append(f"{round(latency_ms)}ms")
    return "|".join(parts)


def add_tag(config: str, latency_ms: float = 0, country: str = "") -> str:
    """افزودن برچسب به fragment و پاک کردن برچسب قبلی."""
    tag = build_tag(latency_ms, country)
    if not tag:
        return config
    base, fragment = split_fragment(config)
    name = fragment.split("|")[0].strip()
    return f"{base}#{name}|{tag}" if name else f"{base}#{tag}"


def get_name(config: str, limit: int = 30) -> str:
    """اسم کانفیگ بدون بخش برچسب."""
    _, fragment = split_fragment(config)
    name = unquote(fragment).split("|")[0].strip()
    return name[:limit]


def get_country(config: str) -> str:
    """کد دوحرفی کشور از برچسب."""
    _, fragment = split_fragment(config)
    for part in fragment.split("|")[1:]:
        part = part.strip()
        if len(part) == 2 and part.isalpha() and part.isupper():
            return part
    return ""


def get_latency(config: str) -> str:
    """رشته‌ی تأخیر از برچسب، مثلاً "84ms"."""
    _, fragment = split_fragment(config)
    for part in fragment.split("|")[1:]:
        part = part.strip()
        if part.endswith("ms") and part[:-2].strip().isdigit():
            return part
    return ""


def get_latency_ms(config: str) -> float:
    """تأخیر به‌صورت عدد؛ بی‌برچسب‌ها inf می‌گیرن تا در sort آخر بیفتن."""
    raw = get_latency(config)
    if not raw:
        return float("inf")
    try:
        return float(raw[:-2].strip())
    except ValueError:
        return float("inf")


def get_security_label(config: str) -> str:
    """Reality / TLS / Other — بر اساس پارامتر security، نه جست‌وجوی متنی.

    نسخه‌ی قبلی در bot.py دنبال رشته‌ی "reality" در *کل* لینک می‌گشت، پس
    کانفیگ TLS ای که کلمه‌ی reality در اسمش بود اشتباه دسته‌بندی می‌شد.
    """
    info = parse(config)
    if info is None:
        return "Other"
    security = info.security
    if security == "reality":
        return "Reality"
    if security in ("tls", "xtls"):
        return "TLS"
    return "Other"


def is_reality(config: str) -> bool:
    return get_security_label(config) == "Reality"


# ─── استخراج از متن خام یا Base64 ──────────────────────────

def try_b64_decode(text: str) -> str:
    """decode یک بلوک Base64، حتی چندخطی یا urlsafe.

    نسخه‌های قبلی در scraper طول *شامل newline* رو برای محاسبه‌ی padding
    به‌کار می‌بردند، پس subscription های چندخطی همیشه binascii.Error می‌دادند
    و بی‌صدا رد می‌شدند.
    """
    clean = _NON_B64.sub(
        "",
        _WHITESPACE.sub("", text).replace("-", "+").replace("_", "/"),
    ).rstrip("=")
    if len(clean) < 8:
        return ""
    try:
        return base64.b64decode(clean + "=" * (-len(clean) % 4)).decode(
            "utf-8", errors="ignore"
        )
    except (binascii.Error, ValueError):
        return ""


def extract_configs(text: str, limit: Optional[int] = None) -> List[str]:
    """همه‌ی لینک‌های vless:// داخل یک متن (یا Base64 آن)."""
    if not text:
        return []
    if SCHEME not in text.lower():
        decoded = try_b64_decode(text)
        if decoded and SCHEME in decoded.lower():
            text = f"{text}\n{decoded}"
    found = re.findall(rf"{re.escape(SCHEME)}[^\s\"'<>\\]+", text, re.IGNORECASE)
    return found[:limit] if limit else found
