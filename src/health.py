"""ثبت سلامت منابع — «کدام منبع چند کانفیگ داد و چه خطایی خورد».

چرا لازم است: منابع عمومی مرتب می‌میرند (repo پاک می‌شود، subscription
۴۰۴ می‌دهد، کانال تلگرام بسته می‌شود). بدون این فایل، تنها نشانه‌ی مردنِ
یک منبع «کم شدن تعداد کل» است که در نویز ۱۰ منبع دیگر گم می‌شود.

الگو از 0xRadikal/Free-v2ray-Configs: خروجی ماشین‌خوان بگذار تا بشود
منبع مرده را حذف کرد بدون خواندن هزار خط لاگ.

عمداً یک رجیستری ساده‌ی سطح-ماژول است، نه پارامتر اضافه روی امضای
scraper ها: آن امضاها (`-> List[str]`) در main و تست‌ها استفاده می‌شوند.
"""
from __future__ import annotations

import threading
from typing import Dict, List

_lock = threading.Lock()
_records: List[Dict[str, object]] = []


def record(kind: str, name: str, count: int = 0, error: str = "") -> None:
    """ثبت نتیجه‌ی یک منبع. kind: web | github | telegram."""
    with _lock:
        _records.append({
            "kind": kind,
            "name": str(name)[:200],
            "count": int(count),
            "ok": not error and count > 0,
            "error": str(error)[:120],
        })


def reset() -> None:
    with _lock:
        _records.clear()


def snapshot() -> Dict[str, object]:
    """خلاصه‌ی قابل نوشتن در health.json."""
    with _lock:
        items = list(_records)

    by_kind: Dict[str, Dict[str, int]] = {}
    for item in items:
        kind = str(item["kind"])
        bucket = by_kind.setdefault(kind, {"sources": 0, "ok": 0, "dead": 0, "configs": 0})
        bucket["sources"] += 1
        bucket["configs"] += int(item["count"])
        if item["ok"]:
            bucket["ok"] += 1
        else:
            bucket["dead"] += 1

    dead = [
        {"kind": i["kind"], "name": i["name"], "error": i["error"] or "صفر کانفیگ"}
        for i in items
        if not i["ok"]
    ]
    top = sorted(items, key=lambda i: -int(i["count"]))[:10]
    return {
        "by_kind": by_kind,
        "dead_sources": dead,
        "top_sources": [
            {"kind": i["kind"], "name": i["name"], "count": i["count"]} for i in top
        ],
        "sources": items,
    }
