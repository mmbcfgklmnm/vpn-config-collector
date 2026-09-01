#!/usr/bin/env python3
"""آپدیت جدول آمار در README.md از روی configs/stats.json.

با `--summary` جدول کوتاه رو در stdout چاپ می‌کنه (برای GITHUB_STEP_SUMMARY).

قبلاً این منطق داخل یه heredoc در workflow بود و سه مشکل داشت:
  ۱. اگه README.md وجود نداشت با FileNotFoundError کرش می‌کرد و هر اجرا قرمز می‌شد.
  ۲. کلید layer4_xray رو می‌خوند که هیچ‌وقت در stats نیست (اسم واقعی layer4_tls).
  ۳. اگه نشانه‌های STATS_START/END در README نبودند بی‌صدا هیچ کاری نمی‌کرد.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATS_PATH = REPO_ROOT / "configs" / "stats.json"
README_PATH = REPO_ROOT / "README.md"

START = "<!-- STATS_START -->"
END = "<!-- STATS_END -->"

DASH = "—"

# کنسول ویندوز پیش‌فرض cp1252 هست و روی متن فارسی/ایموجی
# UnicodeEncodeError می‌ده. رانر گیت‌هاب UTF-8 هست ولی اجرای محلی نه.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

README_TEMPLATE = f"""# 🛡️ VPN Config Collector

کانفیگ‌های VLESS جمع‌آوری‌شده از منابع عمومی که از یک pipeline شش‌لایه
عبور کرده‌اند: فرمت → حذف تکراری → TCP → TLS → Geo → HTTP واقعی.

## آمار آخرین اجرا

{START}
{END}

## استفاده

لینک subscription:

```
https://raw.githubusercontent.com/<owner>/<repo>/main/configs/valid.txt
```

- `configs/valid.txt` — کانفیگ‌هایی که همه لایه‌ها را پاس کرده‌اند
- `configs/all.txt` — همه‌ی کانفیگ‌های خام جمع‌آوری‌شده
- `configs/stats.json` — آمار کامل اجرا

## اجرای محلی

```bash
pip install -r requirements.txt
SKIP_TELEGRAM=true SKIP_XRAY=true python -m src.main
```

برای اجرای لایه ۶ باینری xray لازم است و مسیرش با `XRAY_BINARY_PATH` تنظیم می‌شود.
"""


def load_stats() -> dict:
    try:
        with STATS_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"⚠️ خواندن stats.json ناموفق: {exc}", file=sys.stderr)
        return {}


def layer(stats: dict, name: str, key: str) -> object:
    """یک مقدار از pipeline.<name>.<key> با fallback به خط تیره."""
    return stats.get("pipeline", {}).get(name, {}).get(key, DASH)


def build_rows(stats: dict) -> str:
    ts = str(stats.get("timestamp", ""))[:19].replace("T", " ") or DASH
    return "\n".join(
        [
            "| فیلد | مقدار |",
            "|------|-------|",
            f"| آخرین آپدیت | {ts} UTC |",
            f"| مدت اجرا | {stats.get('duration_seconds', DASH)}s |",
            f"| جمع‌آوری | {stats.get('raw_collected', 0)} |",
            f"| ✅ معتبر | **{stats.get('valid_configs', 0)}** |",
            f"| لایه ۱ فرمت | {layer(stats, 'layer1_format', 'valid')} |",
            f"| لایه ۲ dedup | {layer(stats, 'layer2_dedup', 'unique')} |",
            f"| لایه ۳ TCP | {layer(stats, 'layer3_tcp', 'connected')} |",
            f"| لایه ۴ TLS | {layer(stats, 'layer4_tls', 'passed')} |",
            f"| لایه ۵ Geo | {layer(stats, 'layer5_geo', 'passed')} |",
            f"| لایه ۶ HTTP | {layer(stats, 'layer6_http', 'passed')} |",
        ]
    )


def build_summary(stats: dict) -> str:
    if not stats:
        return "آمار موجود نیست\n"
    return build_rows(stats) + "\n"


def render(content: str, rows: str) -> str:
    """جایگزینی بلوک آمار؛ اگه نشانه‌ها نبودند به انتهای فایل اضافه میشه."""
    block = f"{START}\n{rows}\n{END}"
    if START in content and END in content:
        return re.sub(
            re.escape(START) + r".*?" + re.escape(END),
            lambda _: block,
            content,
            flags=re.DOTALL,
        )
    separator = "" if content.endswith("\n\n") else "\n" if content.endswith("\n") else "\n\n"
    return f"{content}{separator}## آمار آخرین اجرا\n\n{block}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary",
        action="store_true",
        help="چاپ جدول خلاصه در stdout بدون تغییر README",
    )
    args = parser.parse_args()

    stats = load_stats()

    if args.summary:
        sys.stdout.write(build_summary(stats))
        return 0

    if not README_PATH.exists():
        print("ℹ️ README.md نبود، از قالب پیش‌فرض ساخته شد")
        README_PATH.write_text(README_TEMPLATE, encoding="utf-8")

    content = README_PATH.read_text(encoding="utf-8")
    updated = render(content, build_rows(stats))
    if updated != content:
        README_PATH.write_text(updated, encoding="utf-8")
        print("✅ README آپدیت شد")
    else:
        print("ℹ️ README تغییری نداشت")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
