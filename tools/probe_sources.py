#!/usr/bin/env python3
"""سنجش منابع کانفیگ قبل از اضافه کردن به config.py.

چرا: فهرست منابع با حدس بزرگ می‌شود و URL مرده هم سهمیه‌ی زمان اجرا را
می‌خورد و هم لاگ را پر می‌کند. این ابزار هر URL را با همان هدر و همان
استخراج‌کننده‌ی web_scraper می‌زند و می‌گوید چند کانفیگ VLESS و چند
endpoint یکتا داد — بعد فقط برنده‌ها به DIRECT_URLS اضافه می‌شوند.

    python tools/probe_sources.py --current            # منابع فعلی
    python tools/probe_sources.py URL1 URL2            # کاندیدها
    cat candidates.txt | python tools/probe_sources.py # از stdin
    ... --json                                         # خروجی ماشین‌خوان
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp

from src import vless
from src.config import DIRECT_URLS
from src.scraper.web_scraper import HEADERS

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


async def probe(session: aiohttp.ClientSession, url: str) -> Dict[str, object]:
    row: Dict[str, object] = {"url": url, "status": 0, "configs": 0,
                              "endpoints": 0, "error": ""}
    try:
        async with session.get(url, headers=HEADERS, allow_redirects=True) as resp:
            row["status"] = resp.status
            if resp.status != 200:
                row["error"] = f"HTTP {resp.status}"
                return row
            text = await resp.text(encoding="utf-8", errors="ignore")
    except Exception as exc:                      # noqa: BLE001 — گزارش، نه توقف
        row["error"] = type(exc).__name__
        return row

    found = vless.extract_configs(text)
    endpoints = set()
    for cfg in found:
        info = vless.parse(cfg)
        if info and info.host:
            endpoints.add(f"{info.host}:{info.port or 443}")
    row["configs"] = len(found)
    row["endpoints"] = len(endpoints)
    return row


async def run(urls: List[str], timeout: int) -> List[Dict[str, object]]:
    connector = aiohttp.TCPConnector(limit=20)
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(
        connector=connector, timeout=client_timeout
    ) as session:
        return list(await asyncio.gather(*[probe(session, u) for u in urls]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="*", help="URL های کاندید")
    parser.add_argument("--current", action="store_true",
                        help="منابع فعلی config.DIRECT_URLS را بسنج")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--min-configs", type=int, default=1,
                        help="کمتر از این تعداد = رد")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    urls: List[str] = list(args.urls)
    if not sys.stdin.isatty():
        urls += [line.strip() for line in sys.stdin if line.strip()
                 and not line.startswith("#")]
    if args.current:
        urls += list(DIRECT_URLS)
    urls = list(dict.fromkeys(urls))
    if not urls:
        parser.error("URL بده یا --current")

    rows = asyncio.run(run(urls, args.timeout))
    rows.sort(key=lambda r: -int(r["configs"]))

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    ok = [r for r in rows if int(r["configs"]) >= args.min_configs]
    for row in rows:
        mark = "✅" if int(row["configs"]) >= args.min_configs else "❌"
        detail = row["error"] or f"{row['configs']} کانفیگ / {row['endpoints']} endpoint"
        print(f"{mark} {detail:<38} {row['url']}")
    print(f"\n{len(ok)} از {len(rows)} منبع قابل استفاده")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
