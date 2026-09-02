"""تشخیص CDN و اولویت‌بندی endpoint ها.

چرا این ماژول لازم شد — اندازه‌گیری واقعی روی خروجی همین پروژه با نودهای
ایرانی check-host (تهران/اصفهان/شیراز):

    CF-IP + security=none + ws  →  ۲۳ از ۲۴ زنده  (۹۶٪)
    CF-IP (هر security)         →  ۵ از ۸ زنده    (۶۲٪)
    IP خام VPS (لینود/آکامای)   →  ۱ از ۳۰ زنده   (۳٪)

همان ۳۰ endpoint خام از نودهای آلمان/آمریکا/هلند ۳۰ از ۳۰ زنده بودند. یعنی
سرورها *سالم* هستند ولی از ایران بسته‌اند. چون تست‌های محلی pipeline روی رانر
آمریکایی گیت‌هاب اجرا می‌شوند، دقیقاً همان کانفیگ‌هایی را تأیید می‌کردند که
برای مخاطب بی‌فایده‌اند.

نتیجه: endpoint هایی که پشت CDN هستند باید *اول* به تست ایران بروند. سهمیه‌ی
check-host محدود است و ترتیب تصادفی آن را روی کانفیگ‌های مرده هدر می‌دهد.

این فقط اولویت‌بندی است، نه فیلتر: مرجع نهایی خودِ تست ایران است.
"""
from __future__ import annotations

import ipaddress
from typing import Dict, Iterable, List, Optional, Tuple

# ─── محدوده‌های CDN ────────────────────────────────────────
# منبع: https://www.cloudflare.com/ips-v4 (تطبیق داده شده ۲۰۲۶-۰۹)
CLOUDFLARE_V4 = (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
)

CLOUDFLARE_V6 = (
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32",
    "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)

# منبع: https://api.fastly.com/public-ip-list
FASTLY_V4 = (
    "23.235.32.0/20", "43.249.72.0/22", "103.244.50.0/24", "103.245.222.0/23",
    "103.245.224.0/24", "104.156.80.0/20", "140.248.64.0/18", "140.248.128.0/17",
    "146.75.0.0/17", "151.101.0.0/16", "157.52.64.0/18", "167.82.0.0/17",
    "167.82.128.0/20", "167.82.160.0/20", "185.31.16.0/22", "199.27.72.0/21",
)

# Gcore و ArvanCloud — در کانفیگ‌های فارسی زیاد دیده می‌شوند.
OTHER_CDN_V4 = (
    "92.223.64.0/18",      # Gcore
    "5.188.0.0/16",        # Gcore
    "185.143.232.0/22",    # ArvanCloud
    "188.229.116.0/22",    # ArvanCloud
    "94.101.182.0/23",     # ArvanCloud
)

# پورت‌های استاندارد Cloudflare. کانفیگ CF روی پورت غیراستاندارد کار نمی‌کند،
# پس همین‌جا قابل تشخیص است.
CF_HTTP_PORTS = frozenset({80, 8080, 8880, 2052, 2082, 2086, 2095})
CF_TLS_PORTS = frozenset({443, 2053, 2083, 2087, 2096, 8443})
CF_PORTS = CF_HTTP_PORTS | CF_TLS_PORTS


def _networks(*groups: Iterable[str]) -> List[ipaddress._BaseNetwork]:
    nets: List[ipaddress._BaseNetwork] = []
    for group in groups:
        for raw in group:
            try:
                nets.append(ipaddress.ip_network(raw))
            except ValueError:
                continue
    return nets


_CF_NETS = _networks(CLOUDFLARE_V4, CLOUDFLARE_V6)
_CDN_NETS = _networks(CLOUDFLARE_V4, CLOUDFLARE_V6, FASTLY_V4, OTHER_CDN_V4)


def _as_ip(host: str) -> Optional[ipaddress._BaseAddress]:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def is_ip(host: str) -> bool:
    return _as_ip(host) is not None


def is_cloudflare_ip(host: str) -> bool:
    ip = _as_ip(host)
    return ip is not None and any(ip in net for net in _CF_NETS)


def is_cdn_ip(host: str) -> bool:
    """IP متعلق به یکی از CDN های شناخته‌شده."""
    ip = _as_ip(host)
    return ip is not None and any(ip in net for net in _CDN_NETS)


def is_private_or_reserved(host: str) -> bool:
    """IP هایی که هیچ‌وقت سرور عمومی نیستند (۱۰.x، ۱۲۷.x، ۱۶۹.۲۵۴.x …)."""
    ip = _as_ip(host)
    if ip is None:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def classify(host: str, port: int = 0) -> str:
    """دسته‌ی endpoint: cloudflare | cdn | domain | ip."""
    if is_cloudflare_ip(host):
        return "cloudflare"
    if is_cdn_ip(host):
        return "cdn"
    if not is_ip(host):
        return "domain"
    return "ip"


# ─── امتیاز اولویت ────────────────────────────────────────
# عدد کمتر = زودتر به تست ایران می‌رود. اعداد از نرخ زنده‌بودن اندازه‌گیری‌شده
# در بالای همین فایل می‌آیند.
_PRIORITY = {
    "cloudflare": 0,   # ۶۲–۹۶٪ زنده از ایران
    "cdn": 1,
    "domain": 2,       # ممکن است به CDN resolve شود
    "ip": 3,           # ~۳٪ زنده از ایران
}


def priority(host: str, port: int = 0, network: str = "", security: str = "") -> Tuple[int, int]:
    """کلید مرتب‌سازی endpoint برای گرفتن بیشترین نتیجه از سهمیه‌ی check-host.

    کلید دوم داخل هر دسته: ترکیب «CDN + ws + بدون TLS» بالاترین نرخ موفقیت
    اندازه‌گیری‌شده را دارد (۹۶٪)، پس در همان دسته هم جلو می‌افتد.
    """
    kind = classify(host, port)
    base = _PRIORITY.get(kind, 3)

    bonus = 2
    if kind in ("cloudflare", "cdn"):
        ws_like = network in ("ws", "httpupgrade", "xhttp", "grpc")
        if ws_like and security in ("none", ""):
            bonus = 0
        elif ws_like:
            bonus = 1
        if port and port not in CF_PORTS and kind == "cloudflare":
            # پورت غیراستاندارد CF یعنی احتمالاً origin مستقیم است.
            bonus += 2
    return base, bonus


def sort_by_priority(items: List[Dict]) -> List[Dict]:
    """مرتب‌سازی لیستی از dict با کلیدهای host/port/network/security."""
    return sorted(
        items,
        key=lambda it: priority(
            str(it.get("host", "")),
            int(it.get("port") or 0),
            str(it.get("network", "")),
            str(it.get("security", "")),
        ),
    )
