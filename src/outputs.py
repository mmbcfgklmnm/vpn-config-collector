"""نوشتن همه‌ی خروجی‌های اجرا — تنها جایی که به configs/ می‌نویسد.

چرا یک ماژول جدا (الگو از 0xRadikal/Free-v2ray-Configs)
────────────────────────────────────────────────────────
۱. مصرف‌کننده‌ها فرق دارند: کلاینت مدرن فایل متنی می‌خواند، v2rayNG قدیم
   فقط Base64 را درست می‌خواند، و ربات/کانال به «۱۰ تای برتر» نیاز دارد.
۲. index.json قرارداد ماشین‌خوان است: هر مصرف‌کننده باید *آن* را بخواند نه
   مسیرها را hardcode کند. اضافه/جابه‌جا کردن فایل بعداً چیزی را نمی‌شکند.
۳. هیچ فایلی با محتوای خالی نوشته نمی‌شود. یک اجرای ناموفق نباید لینک
   subscription کاربران را خالی کند؛ فایل قبلی سرجایش می‌ماند.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import health, vless
from src.config import (
    ALL_FILE, BY_COUNTRY_DIR, CONFIGS_DIR, HEALTH_FILE, INDEX_FILE,
    INDEX_URL, INTL_B64_FILE, INTL_FILE, IRAN_B64_FILE, IRAN_FILE, POOL_FILE,
    PUBLISH_COUNT, PUBLISH_INTERVAL_MIN, STATS_FILE, SUB_B64_FILE,
    SUB_B64_URL, SUB_INTL_B64_URL, SUB_INTL_URL, SUB_IRAN_B64_URL,
    SUB_IRAN_URL, SUB_MIRROR_URL, SUB_URL, TOP_FILE, VALID_FILE,
)
from src.logger import get_logger

logger = get_logger("outputs")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_text(path: str, text: str) -> bool:
    """نوشتن اتمیک با newline="\\n".

    روی ویندوز پیش‌فرض CRLF است و بعضی کلاینت‌ها لینک subscription را با
    \\r اضافه نمی‌خوانند. نوشتن روی فایل موقت و rename هم جلوی فایل نیمه‌کاره
    را می‌گیرد اگر پروسه وسط نوشتن کشته شود (سقف ۵۵ دقیقه‌ای job).
    """
    if not text.strip():
        logger.warning(f"⏭️  {path} خالی بود — فایل قبلی دست نخورد")
        return False
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    os.replace(tmp, path)
    return True


def write_lines(path: str, lines: Iterable[str]) -> int:
    items = [line.strip() for line in lines if line and line.strip()]
    if not items:
        logger.warning(f"⏭️  {path} خالی بود — فایل قبلی دست نخورد")
        return 0
    write_text(path, "\n".join(items))
    logger.info(f"💾 {len(items)} → {path}")
    return len(items)


def write_b64(path: str, configs: List[str]) -> int:
    """نسخه‌ی Base64 برای کلاینت‌های قدیمی‌تر (v2rayNG قدیم، NekoRay).

    این کلاینت‌ها فهرست متنی خام را نمی‌خوانند و لینک را «نامعتبر» می‌گویند؛
    یک فایل جدا هزینه‌ی صفر دارد و یک دسته کاربر را اضافه می‌کند.
    """
    if not configs:
        return 0
    payload = base64.b64encode(
        ("\n".join(configs) + "\n").encode("utf-8")
    ).decode("ascii")
    write_text(path, payload)
    logger.info(f"💾 base64 ({len(configs)} کانفیگ) → {path}")
    return len(configs)


def write_json(path: str, data: Dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def group_by_country(configs: List[str]) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {}
    for cfg in configs:
        code = vless.get_country(cfg) or "XX"
        groups.setdefault(code, []).append(cfg)
    return groups


def write_countries(configs: List[str]) -> Dict[str, int]:
    """یک فایل به‌ازای هر کشور.

    فایل‌های کشورهایی که این اجرا کانفیگ نداشتند پاک می‌شوند؛ وگرنه کاربر
    فایل کهنه‌ی هفته‌ی قبل را به‌عنوان خروجی تازه برمی‌دارد.
    """
    groups = group_by_country(configs)
    if not groups:
        return {}
    os.makedirs(BY_COUNTRY_DIR, exist_ok=True)

    fresh = {f"{code}.txt" for code in groups}
    for name in os.listdir(BY_COUNTRY_DIR):
        if name.endswith(".txt") and name not in fresh:
            try:
                os.unlink(os.path.join(BY_COUNTRY_DIR, name))
            except OSError:
                pass

    counts: Dict[str, int] = {}
    for code, items in sorted(groups.items()):
        items.sort(key=vless.get_latency_ms)
        write_text(os.path.join(BY_COUNTRY_DIR, f"{code}.txt"), "\n".join(items))
        counts[code] = len(items)
    logger.info(f"💾 {len(counts)} کشور → {BY_COUNTRY_DIR}/")
    return counts


def build_index(
    configs: List[str],
    iran_configs: List[str],
    countries: Dict[str, int],
    stats: Optional[Dict] = None,
    intl_configs: Optional[List[str]] = None,
    pool_configs: Optional[List[str]] = None,
) -> Dict:
    """قرارداد ماشین‌خوان. مصرف‌کننده این را بخواند، نه مسیرها را."""
    intl_configs = intl_configs if intl_configs is not None else []
    pool_configs = pool_configs if pool_configs is not None else []
    return {
        "schema": 2,
        "updated_at": utc_now(),
        "counts": {
            "valid": len(configs),
            "iran_verified": len(iran_configs),
            "international": len(intl_configs),
            "pool": len(pool_configs),
            "top": min(PUBLISH_COUNT, len(configs)),
            "countries": countries,
        },
        "publish": {
            "per_batch": PUBLISH_COUNT,
            "interval_minutes": PUBLISH_INTERVAL_MIN,
        },
        "files": {
            "valid": VALID_FILE,
            "valid_base64": SUB_B64_FILE,
            "iran": IRAN_FILE,
            "iran_base64": IRAN_B64_FILE,
            "international": INTL_FILE,
            "international_base64": INTL_B64_FILE,
            "pool": POOL_FILE,
            "top": TOP_FILE,
            "all_raw": ALL_FILE,
            "stats": STATS_FILE,
            "health": HEALTH_FILE,
            "by_country_dir": BY_COUNTRY_DIR,
        },
        "urls": {
            "valid": SUB_URL,
            "valid_base64": SUB_B64_URL,
            "iran": SUB_IRAN_URL,
            "iran_base64": SUB_IRAN_B64_URL,
            "international": SUB_INTL_URL,
            "international_base64": SUB_INTL_B64_URL,
            "index": INDEX_URL,
            "mirror": SUB_MIRROR_URL,
        },
        "pipeline": (stats or {}).get("pipeline", {}),
    }


def write_all(
    configs: List[str],
    stats: Optional[Dict] = None,
    raw_configs: Optional[List[str]] = None,
    pool_configs: Optional[List[str]] = None,
) -> Dict[str, object]:
    """نوشتن کل خروجی یک اجرا. برمی‌گرداند: خلاصه‌ی چیزی که نوشته شد.

    `pool_configs` پول ذخیره است: کانفیگ‌هایی که لایه‌های ۳ تا ۶ را پاس
    کردند ولی تأیید لایه ۷ ندارند. جدا نوشته می‌شود چون تنها مصرفش پر کردن
    سهمیه‌ی انتشار است و نباید با خروجی تأییدشده قاطی شود.
    """
    os.makedirs(CONFIGS_DIR, exist_ok=True)

    if raw_configs is not None:
        write_lines(ALL_FILE, raw_configs)

    written: Dict[str, object] = {}
    # پول ذخیره مستقل از خروجی اصلی نوشته می‌شود: حتی اجرایی که هیچ کانفیگ
    # تأییدشده‌ای نداشت هم می‌تواند پول ذخیره بدهد، و همان است که سهمیه‌ی
    # ۱۰تایی کانال را نجات می‌دهد.
    if pool_configs:
        pool_ordered = sorted(dict.fromkeys(pool_configs), key=vless.get_latency_ms)
        written["pool"] = write_lines(POOL_FILE, pool_ordered)
    else:
        pool_ordered = []
        written["pool"] = 0

    if not configs:
        # هیچ فایل کانفیگی دست نمی‌خورد؛ فقط سلامت منابع ثبت می‌شود تا
        # بشود فهمید چرا اجرا خالی بود.
        logger.warning("⚠️ خروجی خالی — فایل‌های قبلی حفظ شدند")
        write_json(HEALTH_FILE, {"updated_at": utc_now(), **health.snapshot()})
        return {"valid": 0, "iran": 0, "international": 0,
                "pool": written["pool"], "top": 0, "countries": {}}

    ordered = sorted(configs, key=vless.get_latency_ms)
    # تفکیک داخلی/خارجی در انتهای مسیر تولید اشتراک. مرز دقیقاً همان برچسب
    # IR است: لایه ۴ از نود ایرانی دیده که این endpoint جواب می‌دهد.
    iran_configs = [c for c in ordered if vless.is_iran_verified(c)]
    intl_configs = [c for c in ordered if not vless.is_iran_verified(c)]

    written["valid"] = write_lines(VALID_FILE, ordered)
    write_b64(SUB_B64_FILE, ordered)

    if iran_configs:
        written["iran"] = write_lines(IRAN_FILE, iran_configs)
        write_b64(IRAN_B64_FILE, iran_configs)
    else:
        written["iran"] = 0

    if intl_configs:
        written["international"] = write_lines(INTL_FILE, intl_configs)
        write_b64(INTL_B64_FILE, intl_configs)
    else:
        written["international"] = 0

    # top: اول کانفیگ‌های تأییدشده‌ی ایران، بعد بقیه — همان ترتیبی که
    # کانال پست می‌کند.
    top_pool = iran_configs + intl_configs
    written["top"] = write_lines(TOP_FILE, top_pool[:PUBLISH_COUNT])

    countries = write_countries(ordered)
    written["countries"] = countries

    write_json(INDEX_FILE, build_index(
        ordered, iran_configs, countries, stats, intl_configs, pool_ordered,
    ))
    write_json(HEALTH_FILE, {"updated_at": utc_now(), **health.snapshot()})
    logger.info(
        f"📦 خروجی‌ها نوشته شد | valid={written['valid']} "
        f"iran={written['iran']} intl={written['international']} "
        f"pool={written['pool']} top={written['top']} کشور={len(countries)}"
    )
    return written
