#!/usr/bin/env python3
"""فهرست نودهای check-host.net — برای تأیید اینکه کدام نود ایرانی واقعاً هست.

اسم نودها در مستند API لیست نشده و دستی کشف می‌شود (مثلاً ir6 وجود ندارد).
این ابزار همان را از خودِ API می‌پرسد تا CHECKHOST_NODES با حدس ست نشود.

    python tools/checkhost_nodes.py           # فقط نودهای ایران
    python tools/checkhost_nodes.py --all     # همه‌ی کشورها
    python tools/checkhost_nodes.py --env     # خط آماده برای CHECKHOST_NODES

تنها جای پروژه که موقع اجرا به شبکه وصل می‌شود و در pipeline صدا زده نمی‌شود.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

URL = "https://check-host.net/nodes/hosts"
# با User-Agent پیش‌فرض urllib سایت ۴۰۳ می‌دهد (اندازه‌گیری‌شده). aiohttp که
# در pipeline استفاده می‌شود UA خودش را می‌فرستد و مشکلی ندارد.
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "vpn-config-collector (+https://github.com/mmbcfgklmnm/vpn-config-collector)",
}

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


def fetch(timeout: float = 15.0) -> dict:
    request = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
        data = json.load(resp)
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, dict):
        raise RuntimeError("پاسخ نامعتبر از check-host")
    return nodes


def country_of(info: object) -> str:
    """location = [کد کشور, نام کشور, شهر] طبق مستند."""
    if isinstance(info, dict):
        location = info.get("location")
        if isinstance(location, list) and location:
            return str(location[0]).lower()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", default="ir", help="کد کشور، پیش‌فرض ir")
    parser.add_argument("--all", action="store_true", help="همه‌ی کشورها")
    parser.add_argument("--env", action="store_true",
                        help="چاپ به شکل CHECKHOST_NODES=a,b,c")
    args = parser.parse_args()

    try:
        nodes = fetch()
    except Exception as exc:
        print(f"❌ گرفتن فهرست نودها ناموفق: {exc}", file=sys.stderr)
        return 1

    wanted = sorted(
        name for name, info in nodes.items()
        if args.all or country_of(info) == args.country.lower()
    )
    if not wanted:
        print(f"⚠️ هیچ نودی برای «{args.country}» نبود (کل: {len(nodes)})")
        return 1

    if args.env:
        print(f"CHECKHOST_NODES={','.join(wanted)}")
        return 0

    print(f"🌐 {len(wanted)} نود از {len(nodes)}:")
    for name in wanted:
        info = nodes.get(name) or {}
        location = info.get("location") or []
        city = location[2] if len(location) > 2 else "?"
        print(f"  {name:<32} {city:<14} {info.get('ip', '?'):<16} {info.get('asn', '?')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
