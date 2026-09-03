"""صف کانفیگ‌های اهدایی کاربران — ذخیره، اعتبارسنجی، سهمیه.

قرارداد (خواسته‌ی صریح کاربر)
─────────────────────────────
هر دوره ۱۰ کانفیگ اصلی + *۲ کانفیگ اهدایی اضافه* پست می‌شود، و هر کانفیگ
اهدایی **هرگز بیش از یک بار** پست نمی‌شود. پس صف باید ماندگار باشد و وضعیت
هر آیتم را نگه دارد: queued → taken → sent.

«taken» چرا لازم است: اگر فقط بعد از ارسال موفق علامت می‌زدیم، کشته‌شدن
پروسه وسط دوره یعنی همان کانفیگ دوباره انتخاب می‌شود. اگر فقط قبل از ارسال
علامت می‌زدیم، هیچ‌وقت نمی‌فهمیدیم واقعاً رفت یا نه. دو مرحله هم انتخابِ
یکتا را تضمین می‌کند هم رکورد واقعیت را نگه می‌دارد.

حریم خصوصی
──────────
شناسه‌ی تلگرام کاربر *ذخیره نمی‌شود*. فقط sha256(salt + id) کوتاه‌شده ذخیره
می‌شود تا سهمیه‌ی روزانه و ردگیری سوءاستفاده ممکن باشد. salt در فایل جدا و
gitignore شده است، پس هش‌های این فایل بدون آن قابل برگرداندن نیستند. خودِ
فایل صف هم commit نمی‌شود.

امنیت محتوا
───────────
۱. اسم انتخابیِ اهداکننده حذف می‌شود (fragment بازنویسی می‌شود). دلیل: آن
   متن بعداً در کانال رندر می‌شود و نباید بردار تزریق entity/لینک باشد.
۲. همان اعتبارسنجی لایه ۱ اجرا می‌شود، پس IP خصوصی/لوکال/رزرو رد می‌شود —
   وگرنه یک کانفیگ اهدایی با host=127.0.0.1 یا 169.254.169.254 تست TCP ما
   را به شبکه‌ی داخلی رانر می‌فرستاد.
۳. میزبان‌های داخلیِ نام‌دار (localhost، .local، .internal) هم رد می‌شوند.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import vless
from src.config import (
    DONATE_MAX_PER_DAY, DONATE_MAX_PER_MSG, DONATE_MIN_GAP_SEC,
    DONATE_QUEUE_MAX, DONATIONS_FILE, PUBLISH_DONATED_COUNT,
)
from src.logger import get_logger
from src.tester.format_validator import validate_vless

logger = get_logger("donations")

SCHEMA = 1
MAX_CONFIG_LEN = 1024

QUEUED, TAKEN, SENT = "queued", "taken", "sent"

# میزبان‌هایی که هرگز سرور عمومی نیستند و اسم‌شان IP نیست (پس چک IP خصوصی
# آن‌ها را نمی‌گیرد).
_INTERNAL_HOST = re.compile(
    r"(^|\.)(localhost|local|internal|lan|home|corp|intranet)$", re.IGNORECASE
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _salt_path() -> str:
    return os.path.join(os.path.dirname(DONATIONS_FILE) or ".", ".donate_salt")


def _salt() -> str:
    """salt هش اهداکننده. از env، وگرنه یک‌بار ساخته و روی دیسک می‌ماند."""
    env = os.getenv("DONATE_SALT", "").strip()
    if env:
        return env
    path = _salt_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            saved = fh.read().strip()
        if saved:
            return saved
    except OSError:
        pass
    fresh = secrets.token_hex(16)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(fresh + "\n")
    except OSError as exc:
        logger.warning(f"⚠️ ذخیره‌ی salt ناموفق: {exc} — سهمیه‌ی روزانه موقتی می‌شود")
    return fresh


def donor_hash(user_id: object) -> str:
    """شناسه‌ی یک‌طرفه‌ی اهداکننده. برگرداندنش بدون salt ممکن نیست."""
    digest = hashlib.sha256(f"{_salt()}:{user_id}".encode("utf-8")).hexdigest()
    return digest[:12]


def _key(config: str) -> str:
    """کلید یکتایی — بدنه‌ی لینک بدون fragment."""
    base, _ = vless.split_fragment(config.strip())
    return hashlib.sha1(base.encode("utf-8", "ignore")).hexdigest()[:16]


# ─── ذخیره‌سازی ────────────────────────────────────────────

def _empty() -> Dict:
    return {"schema": SCHEMA, "items": [], "donors": {}}


def _load() -> Dict:
    try:
        with open(DONATIONS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    items = data.get("items")
    donors = data.get("donors")
    data["items"] = [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []
    data["donors"] = donors if isinstance(donors, dict) else {}
    data.setdefault("schema", SCHEMA)
    return data


def _save(data: Dict) -> bool:
    directory = os.path.dirname(DONATIONS_FILE) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        tmp = f"{DONATIONS_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, DONATIONS_FILE)
        return True
    except OSError as exc:
        logger.error(f"❌ ذخیره‌ی صف اهدایی ناموفق: {exc}")
        return False


# ─── اعتبارسنجی ───────────────────────────────────────────

def sanitize(config: str) -> Tuple[str, str]:
    """کانفیگ اهدایی → (نسخه‌ی پاک‌شده, دلیل رد).

    اسم انتخابیِ اهداکننده دور ریخته می‌شود: آن رشته در کانال رندر می‌شود و
    ورودی کاربر نباید مستقیم به متن پیام برسد.
    """
    config = (config or "").strip()
    if not config:
        return "", "خالی"
    if len(config) > MAX_CONFIG_LEN:
        return "", "طول غیرعادی"
    if any(ch in config for ch in ("\n", "\r", "\t")):
        return "", "کاراکتر غیرمجاز"

    ok, reason = validate_vless(config)
    if not ok:
        return "", reason

    info = vless.parse(config)
    if info is None:
        return "", "پارس ناموفق"
    if _INTERNAL_HOST.search(info.host):
        return "", "میزبان داخلی"

    base, _ = vless.split_fragment(config)
    return f"{base}#Donated-{vless.short_id(config)}", ""


# ─── سهمیه‌ی اهداکننده ─────────────────────────────────────

def _quota(data: Dict, donor: str) -> Tuple[bool, str, Dict]:
    """آیا این اهداکننده اجازه‌ی اهدای دیگری دارد؟"""
    record = data["donors"].get(donor)
    if not isinstance(record, dict):
        record = {"day": _today(), "count": 0, "last": 0.0}
    if record.get("day") != _today():
        record = {"day": _today(), "count": 0, "last": record.get("last", 0.0)}

    gap = time.time() - float(record.get("last") or 0.0)
    if DONATE_MIN_GAP_SEC > 0 and gap < DONATE_MIN_GAP_SEC:
        return False, f"کمی صبر کن ({DONATE_MIN_GAP_SEC - int(gap)}s)", record
    if DONATE_MAX_PER_DAY > 0 and int(record.get("count", 0)) >= DONATE_MAX_PER_DAY:
        return False, f"سهمیه‌ی روزانه پر شد ({DONATE_MAX_PER_DAY})", record
    return True, "", record


# ─── افزودن ───────────────────────────────────────────────

def add(configs: List[str], user_id: object) -> Dict[str, object]:
    """افزودن کانفیگ‌های یک کاربر به صف.

    خروجی: شمارش added/duplicate/invalid + دلیل‌ها، برای پیام پاسخ ربات.
    """
    result: Dict[str, object] = {
        "added": 0, "duplicate": 0, "invalid": 0,
        "reasons": {}, "blocked": "", "queued_total": 0,
    }
    data = _load()
    donor = donor_hash(user_id)
    allowed, why, record = _quota(data, donor)
    if not allowed:
        result["blocked"] = why
        result["queued_total"] = sum(
            1 for it in data["items"] if it.get("status") == QUEUED
        )
        return result

    if DONATE_QUEUE_MAX > 0:
        pending = sum(1 for it in data["items"] if it.get("status") == QUEUED)
        if pending >= DONATE_QUEUE_MAX:
            result["blocked"] = "صف اهدایی پر است"
            result["queued_total"] = pending
            return result

    known = {it.get("key") for it in data["items"]}
    reasons: Dict[str, int] = {}
    room = max(0, DONATE_MAX_PER_DAY - int(record.get("count", 0))) \
        if DONATE_MAX_PER_DAY > 0 else len(configs)
    limit = min(DONATE_MAX_PER_MSG or len(configs), room or 0)

    for raw in configs[: max(0, limit)]:
        clean, reason = sanitize(raw)
        if not clean:
            result["invalid"] = int(result["invalid"]) + 1
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        key = _key(clean)
        if key in known:
            result["duplicate"] = int(result["duplicate"]) + 1
            continue
        known.add(key)
        data["items"].append({
            "key": key,
            "config": clean,
            "donor": donor,
            "at": _utc_now(),
            "status": QUEUED,
        })
        result["added"] = int(result["added"]) + 1

    if result["added"]:
        record["count"] = int(record.get("count", 0)) + int(result["added"])
        record["last"] = time.time()
        data["donors"][donor] = record
        _save(data)

    result["reasons"] = dict(sorted(reasons.items(), key=lambda x: -x[1])[:3])
    result["queued_total"] = sum(
        1 for it in data["items"] if it.get("status") == QUEUED
    )
    return result


# ─── برداشت برای یک دوره ──────────────────────────────────

def take_for_cycle(count: int) -> List[str]:
    """قدیمی‌ترین `count` کانفیگ صف را برمی‌دارد و `taken` علامت می‌زند.

    FIFO است تا اهداکننده‌ای که دیروز فرستاده جلوتر از امروزی باشد.

    اگر ذخیره‌سازی ناموفق شد، *هیچ‌چیز* برنمی‌گردد. دلیل: قرارداد صریح کاربر
    «هر اهدایی حداکثر یک بار» است. اگر با علامتِ ذخیره‌نشده پست کنیم، دوره‌ی
    بعد همان‌ها دوباره انتخاب می‌شوند؛ از دست دادن یک دوره‌ی اهدایی از پست
    تکراری بهتر است.
    """
    if count <= 0:
        return []
    data = _load()
    picked: List[Dict] = []
    for item in data["items"]:
        if item.get("status") != QUEUED:
            continue
        if not isinstance(item.get("config"), str) or not item["config"]:
            continue
        picked.append(item)
        if len(picked) >= count:
            break
    if not picked:
        return []

    for item in picked:
        item["status"] = TAKEN
        item["taken_at"] = _utc_now()
    if not _save(data):
        logger.error("❌ علامت‌گذاری taken ذخیره نشد — اهدایی این دوره پست نمی‌شود")
        return []
    logger.info(f"🎁 {len(picked)} کانفیگ اهدایی برای این دوره برداشته شد")
    return [item["config"] for item in picked]


# ─── ثبت ارسال ────────────────────────────────────────────

def mark_sent(configs: List[str]) -> int:
    """کانفیگ‌هایی که واقعاً در کانال پست شدند را `sent` می‌کند.

    کلید از خودِ کانفیگ بازمحاسبه می‌شود (تابع `_key` قطعی است)، پس صداکننده
    لازم نیست کلید داخلی صف را حمل کند.
    """
    wanted = {_key(c) for c in configs if isinstance(c, str) and c.strip()}
    if not wanted:
        return 0
    data = _load()
    changed = 0
    for item in data["items"]:
        if item.get("key") in wanted and item.get("status") != SENT:
            item["status"] = SENT
            item["sent_at"] = _utc_now()
            changed += 1
    if changed:
        _save(data)
    return changed


def requeue_taken() -> int:
    """آیتم‌های گیرافتاده در `taken` را به صف برمی‌گرداند — فقط دستی.

    اگر پروسه بین برداشت و ارسال کشته شود، آیتم برای همیشه `taken` می‌ماند.
    این تابع *خودکار صدا نمی‌شود*: نمی‌دانیم آن کانفیگ پست شد یا نه، و
    برگرداندن خودکارش می‌تواند قرارداد «حداکثر یک بار» را بشکند. ادمین با
    دیدن آمار تصمیم می‌گیرد.
    """
    data = _load()
    changed = 0
    for item in data["items"]:
        if item.get("status") == TAKEN:
            item["status"] = QUEUED
            item.pop("taken_at", None)
            changed += 1
    if changed and _save(data):
        logger.info(f"↩️ {changed} اهدایی از taken به صف برگشت")
    return changed


# ─── آمار و نگهداری ───────────────────────────────────────

def queued_count() -> int:
    return sum(1 for it in _load()["items"] if it.get("status") == QUEUED)


def stats() -> Dict[str, object]:
    """خلاصه‌ی صف برای پنل ادمین. هیچ شناسه‌ی کاربری در خروجی نیست."""
    data = _load()
    items = data["items"]
    counts = {QUEUED: 0, TAKEN: 0, SENT: 0}
    for item in items:
        status = item.get("status")
        if status in counts:
            counts[status] += 1
    return {
        "queued": counts[QUEUED],
        "taken": counts[TAKEN],
        "sent": counts[SENT],
        "total": len(items),
        "donors": len(data["donors"]),
        "cycles_left": counts[QUEUED] // max(1, PUBLISH_DONATED_COUNT),
    }


def purge_sent(keep: int = 200) -> int:
    """قدیمی‌ترین رکوردهای `sent` را حذف می‌کند.

    رکورد ارسال‌شده باید بماند تا کانفیگ تکراری دوباره وارد صف نشود، ولی
    بی‌سقف نگه داشتنش فایل را بی‌جهت بزرگ می‌کند. `keep` تای آخر می‌ماند.
    """
    data = _load()
    sent_idx = [i for i, it in enumerate(data["items"]) if it.get("status") == SENT]
    extra = len(sent_idx) - max(0, keep)
    if extra <= 0:
        return 0
    drop = set(sent_idx[:extra])
    data["items"] = [it for i, it in enumerate(data["items"]) if i not in drop]
    if _save(data):
        logger.info(f"🧹 {extra} رکورد ارسال‌شده‌ی قدیمی حذف شد")
        return extra
    return 0





