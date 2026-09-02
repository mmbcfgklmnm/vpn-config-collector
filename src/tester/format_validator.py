"""
لایه ۱: اعتبارسنجی فرمت VLESS

قاعده‌ی security عوض شد
───────────────────────
قبلاً هر کانفیگی که security اش reality/tls/xtls نبود رد می‌شد. اندازه‌گیری
روی پول واقعی: بزرگ‌ترین دلیل رد شدن «security نامعتبر: none» با ~۸۰۰ کانفیگ
بود. ولی تست با نودهای ایرانی check-host نشان داد همان دسته
(IP کلادفلر + transport مبتنی بر HTTP + بدون TLS) بالاترین نرخ دسترسی از
ایران را دارد: ۲۳ از ۲۴ در برابر ۱ از ۳۰ برای IP خام VPS.

پس security=none حالا *فقط* در همین شرط پذیرفته می‌شود:
    endpoint پشت CDN باشد  و  transport مبتنی بر HTTP باشد (ws/httpupgrade/
    xhttp/grpc). این همان الگوی «VLESS over WS از طریق Cloudflare» است.
بیرون از این شرط، security=none یعنی ترافیک لخت روی اینترنت و همچنان رد
می‌شود. با ALLOW_CDN_PLAIN=0 می‌شود کل این استثنا را خاموش کرد.
"""
import ipaddress
import re
from typing import List, Tuple

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import cdn, vless
from src.config import ALLOW_CDN_PLAIN, ALLOWED_SECURITY, CDN_PLAIN_NETWORKS
from src.logger import get_logger

logger = get_logger("format_validator")

UUID_RE = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
    re.IGNORECASE,
)

DOMAIN_RE = re.compile(
    r"^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)

# UUID های dummy که معمولاً fake هستن
DUMMY_UUIDS = {
    "00000000-0000-0000-0000-000000000000",
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
}

MIN_LEN = 30
MAX_LEN = 2000


def validate_host(host: str) -> bool:
    """درستی host: IPv4، IPv6 یا دامنه.

    urlparse براکت‌های IPv6 رو حذف می‌کنه، پس چک قبلی که دنبال "["
    می‌گشت هیچ‌وقت اجرا نمی‌شد و همه‌ی کانفیگ‌های IPv6 رد می‌شدند.
    """
    if not host or len(host) > 253:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    return bool(DOMAIN_RE.match(host))


def is_cdn_plain(info: "vless.Vless") -> bool:
    """آیا security=none برای این کانفیگ قابل قبول است؟

    شرط: transport مبتنی بر HTTP + endpoint پشت CDN. host دامنه‌ای هم قبول
    است چون خیلی از کانفیگ‌های CF با دامنه نوشته می‌شوند و resolve کردن
    اینجا (لایه‌ی همگام روی هزاران کانفیگ) گران است؛ مرجع نهایی تست ایران
    در لایه‌ی بعد است، این فقط اجازه‌ی ورود می‌دهد.
    """
    if not ALLOW_CDN_PLAIN:
        return False
    if info.network not in CDN_PLAIN_NETWORKS:
        return False
    if cdn.is_cdn_ip(info.host):
        return True
    # دامنه: پورت استاندارد CDN شرط لازم است تا هر دامنه‌ی بی‌TLS رد نشود.
    return not cdn.is_ip(info.host) and info.port in cdn.CF_PORTS


def validate_vless(config: str) -> Tuple[bool, str]:
    config = config.strip()

    if not config.lower().startswith(vless.SCHEME):
        return False, "پروتکل vless نیست"

    if not (MIN_LEN <= len(config) <= MAX_LEN):
        return False, "طول نامعتبر"

    info = vless.parse(config)
    if info is None:
        return False, "پارس ناموفق"

    if not UUID_RE.match(info.uuid):
        return False, f"UUID نامعتبر: {info.uuid[:20]}"
    if info.uuid.lower() in DUMMY_UUIDS:
        return False, "UUID dummy"

    if not validate_host(info.host):
        return False, f"host نامعتبر: {info.host}"

    # ۱۰.x، ۱۲۷.x، ۱۹۲.۱۶۸.x … هیچ‌وقت سرور عمومی نیستند. قبلاً این‌ها تا
    # لایه‌ی TCP می‌رفتند و روی رانر گیت‌هاب حتی وصل هم می‌شدند (به خود رانر!).
    if cdn.is_private_or_reserved(info.host):
        return False, "IP خصوصی/رزرو"

    if not (1 <= info.port <= 65535):
        return False, f"port نامعتبر: {info.port}"

    # security — مهم‌ترین فیلتر
    security = info.security
    if security not in ALLOWED_SECURITY:
        if not is_cdn_plain(info):
            return False, f"security نامعتبر: {security}"

    # reality بدون کلید عمومی قابل استفاده نیست
    if security == "reality" and not info.params.get("pbk"):
        return False, "reality بدون pbk"

    return True, ""


def filter_by_format(configs: List[str]) -> Tuple[List[str], dict]:
    valid = []
    reasons: dict = {}
    cdn_plain = 0
    for cfg in configs:
        ok, reason = validate_vless(cfg)
        if ok:
            valid.append(cfg.strip())
            info = vless.parse(cfg)
            if info is not None and info.security not in ALLOWED_SECURITY:
                cdn_plain += 1
        else:
            reasons[reason] = reasons.get(reason, 0) + 1

    stats = {
        "total": len(configs),
        "valid": len(valid),
        "invalid": len(configs) - len(valid),
        # چند کانفیگ فقط به‌خاطر استثنای CDN وارد شدند — برای دیدن اثر
        # ALLOW_CDN_PLAIN در stats.json.
        "cdn_plain": cdn_plain,
        "top_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])[:5]),
    }
    logger.info(
        f"لایه ۱ (فرمت): {stats['valid']}/{stats['total']} معتبر"
        + (f" | {cdn_plain} تا از مسیر CDN-plain" if cdn_plain else "")
    )
    return valid, stats
