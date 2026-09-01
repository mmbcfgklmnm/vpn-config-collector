"""
لایه ۲: حذف تکراری هوشمند
سه سطح: exact → normalized → fingerprint

اثرانگشت قبلی فقط host:port:uuid بود؛ یعنی دو کانفیگ روی همان سرور با
transport متفاوت (ws در برابر tcp، sni یا path متفاوت) یکی حساب می‌شدند و
یکی‌شان — که ممکن بود همان یکی کار کند — حذف می‌شد. حالا فیلدهایی که
اتصال را تعیین می‌کنند هم در اثرانگشت هستند.
"""
import hashlib
from typing import List, Tuple

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.logger import get_logger

logger = get_logger("deduplicator")

# پارامترهایی که مسیر واقعی اتصال را تعیین می‌کنند.
FP_PARAMS = (
    "security",
    "type",
    "sni",
    "host",
    "path",
    "servicename",
    "headertype",
    "flow",
    "pbk",
    "sid",
)


def get_fingerprint(config: str) -> str:
    """اثرانگشت اتصال، مستقل از اسم (fragment) و ترتیب پارامترها."""
    info = vless.parse(config)
    if info is None:
        return "raw:" + hashlib.md5(config.encode("utf-8", "replace")).hexdigest()

    parts = [
        info.host.lower(),
        str(info.port),
        info.uuid.lower(),
    ]
    # مقدار پارامترها case-sensitive است (path و sni)، پس lowercase نمی‌کنیم.
    parts.extend(info.params.get(key, "") for key in FP_PARAMS)
    return "|".join(parts)


def normalize(config: str) -> str:
    """شکل یکسان‌شده‌ی لینک: بدون fragment، با پارامترهای مرتب‌شده.

    نسخه‌ی قبلی کل رشته را lowercase می‌کرد (پس `path=/WS` و `path=/ws`
    یکی می‌شدند در حالی که سرور آن‌ها را جدا می‌بیند) و با urlencode مقدارها
    را دوباره encode می‌کرد.
    """
    info = vless.parse(config)
    if info is None:
        return vless.split_fragment(config)[0].strip()

    query = "&".join(f"{k}={info.params[k]}" for k in sorted(info.params))
    return f"{info.uuid.lower()}@{info.host.lower()}:{info.port}?{query}"


def deduplicate(configs: List[str]) -> Tuple[List[str], dict]:
    unique = []
    seen_exact: set = set()
    seen_norm: set = set()
    seen_fp: set = set()

    exact_dups = norm_dups = fp_dups = 0

    for cfg in configs:
        cfg = cfg.strip()
        if not cfg:
            continue

        # سطح ۱: exact
        h = hashlib.sha256(cfg.encode("utf-8", "replace")).hexdigest()
        if h in seen_exact:
            exact_dups += 1
            continue
        seen_exact.add(h)

        # سطح ۲: normalized
        nh = normalize(cfg)
        if nh in seen_norm:
            norm_dups += 1
            continue
        seen_norm.add(nh)

        # سطح ۳: fingerprint
        fp = get_fingerprint(cfg)
        if fp in seen_fp:
            fp_dups += 1
            continue
        seen_fp.add(fp)

        unique.append(cfg)

    stats = {
        "total": len(configs),
        "unique": len(unique),
        "exact_dups": exact_dups,
        "norm_dups": norm_dups,
        "fp_dups": fp_dups,
    }
    logger.info(
        f"لایه ۲ (dedup): {stats['unique']}/{stats['total']} | "
        f"exact:{exact_dups} norm:{norm_dups} fp:{fp_dups}"
    )
    return unique, stats
