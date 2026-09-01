"""
لایه ۱: اعتبارسنجی فرمت VLESS
فقط vless:// با security=reality یا tls/xtls قبول میشه
"""
import ipaddress
import re
from typing import List, Tuple

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.config import ALLOWED_SECURITY
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

    if not (1 <= info.port <= 65535):
        return False, f"port نامعتبر: {info.port}"

    # security — مهم‌ترین فیلتر
    security = info.security
    if security not in ALLOWED_SECURITY:
        return False, f"security نامعتبر: {security}"

    # reality بدون کلید عمومی قابل استفاده نیست
    if security == "reality" and not info.params.get("pbk"):
        return False, "reality بدون pbk"

    return True, ""


def filter_by_format(configs: List[str]) -> Tuple[List[str], dict]:
    valid = []
    reasons: dict = {}
    for cfg in configs:
        ok, reason = validate_vless(cfg)
        if ok:
            valid.append(cfg.strip())
        else:
            reasons[reason] = reasons.get(reason, 0) + 1

    stats = {
        "total": len(configs),
        "valid": len(valid),
        "invalid": len(configs) - len(valid),
        "top_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])[:5]),
    }
    logger.info(f"لایه ۱ (فرمت): {stats['valid']}/{stats['total']} معتبر")
    return valid, stats
