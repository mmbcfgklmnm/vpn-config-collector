"""
VPN Config Collector v2 - Pipeline ۶ لایه
هدف: کمیت پایین، کیفیت بالا

اصلاحات این نسخه:
  ۱. SKIP_XRAY/SKIP_TELEGRAM از config خوانده میشن (قبلاً os.getenv مستقیم
     بود و با مقدارهایی مثل "1" یا "TRUE" کار نمی‌کرد).
  ۲. ورودی لایه ۶ با MAX_HTTP_TEST محدود میشه و سریع‌ترین‌ها انتخاب میشن؛
     قبلاً هر تعداد کانفیگ به xray داده می‌شد و job با سقف ۵۵ دقیقه
     نیمه‌کاره کشته می‌شد (و هیچ فایلی commit نمی‌شد).
  ۳. خروجی نهایی بر اساس تأخیر مرتب میشه.
  ۴. اگه pipeline صفر کانفیگ داد، valid.txt قبلی *پاک نمیشه* — لینک
     subscription کاربران با یک اجرای ناموفق از کار نمی‌افتد.
  ۵. آمار حتی در صورت خطا نوشته میشه تا README و خلاصه‌ی اجرا گویا باشند.
"""
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import vless
from src.config import (
    VALID_FILE, ALL_FILE, STATS_FILE, CONFIGS_DIR,
    MAX_HTTP_TEST, SKIP_XRAY, SKIP_TELEGRAM,
)
from src.logger import get_logger
from src.scraper.web_scraper import scrape_web
from src.scraper.github_scraper import scrape_github
from src.scraper.telegram_scraper import scrape_telegram
from src.tester.format_validator import filter_by_format
from src.tester.deduplicator import deduplicate
from src.tester.tcp_tester import test_tcp_batch
from src.tester.tls_tester import test_tls_batch
from src.tester.geo_checker import check_geo_batch
from src.tester.http_tester import http_test_batch
from src.publisher.publisher import Publisher

logger = get_logger("main")


def save(configs: List[str], path: str) -> None:
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(configs) + ("\n" if configs else ""))
    logger.info(f"💾 {len(configs)} → {path}")


def save_stats(stats: Dict) -> None:
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    with open(STATS_FILE, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)


def simple_dedup(configs: List[str]) -> List[str]:
    seen = set()
    result = []
    for cfg in configs:
        cfg = cfg.strip()
        if cfg and cfg not in seen:
            seen.add(cfg)
            result.append(cfg)
    return result


async def collect() -> List[str]:
    logger.info("=" * 55)
    logger.info("🚀 فاز جمع‌آوری")
    logger.info("=" * 55)
    started = time.monotonic()

    results = await asyncio.gather(
        scrape_web(), scrape_github(), scrape_telegram(),
        return_exceptions=True,
    )

    all_configs: List[str] = []
    for name, result in zip(["وب", "GitHub", "تلگرام"], results):
        if isinstance(result, list):
            all_configs.extend(result)
            logger.info(f"  {name}: {len(result)}")
        else:
            logger.warning(f"  {name}: خطا — {result}")

    unique = simple_dedup(all_configs)
    logger.info(
        f"\n✅ {len(all_configs)} کل | {len(unique)} یکتا | "
        f"{time.monotonic() - started:.1f}s"
    )
    save(unique, ALL_FILE)
    return unique


async def pipeline(configs: List[str]) -> Tuple[List[str], Dict]:
    logger.info("\n🔬 Pipeline ۶ لایه")
    logger.info(f"   ورودی: {len(configs)}\n")
    stats: Dict = {}

    logger.info("1️⃣  فرمت + فیلتر VLESS...")
    configs, s1 = filter_by_format(configs)
    stats["layer1_format"] = s1
    logger.info(f"   → {len(configs)}\n")
    if not configs:
        return [], stats

    logger.info("2️⃣  حذف تکراری...")
    configs, s2 = deduplicate(configs)
    stats["layer2_dedup"] = s2
    logger.info(f"   → {len(configs)}\n")
    if not configs:
        return [], stats

    logger.info("3️⃣  تست TCP...")
    configs, s3 = await test_tcp_batch(configs)
    stats["layer3_tcp"] = s3
    logger.info(f"   → {len(configs)}\n")
    if not configs:
        return [], stats

    logger.info("4️⃣  تست TLS Handshake...")
    configs_ms, s4 = await test_tls_batch(configs)
    stats["layer4_tls"] = s4
    tls_ms_map = dict(configs_ms)
    configs = [c for c, _ in configs_ms]
    logger.info(f"   → {len(configs)}\n")
    if not configs:
        return [], stats

    logger.info("5️⃣  بررسی Geo...")
    configs_country, s5 = await check_geo_batch(configs)
    stats["layer5_geo"] = s5
    country_map = dict(configs_country)
    configs = [c for c, _ in configs_country]
    logger.info(f"   → {len(configs)}\n")
    if not configs:
        return [], stats

    if SKIP_XRAY:
        logger.info("6️⃣  HTTP رد شد (SKIP_XRAY)")
        stats["layer6_http"] = {
            "total": len(configs),
            "passed": len(configs),
            "skipped": True,
            "avg_ms": 0,
        }
        final = [
            vless.add_tag(c, tls_ms_map.get(c, 0), country_map.get(c, ""))
            for c in configs
        ]
    else:
        # xray سنگین‌ترین لایه است؛ سریع‌ترین‌ها را تا سقف MAX_HTTP_TEST
        # تست می‌کنیم. بقیه تست‌نشده‌اند، پس در خروجی نهایی نمی‌آیند.
        candidates = sorted(configs, key=lambda c: tls_ms_map.get(c, float("inf")))
        dropped = max(0, len(candidates) - MAX_HTTP_TEST)
        candidates = candidates[:MAX_HTTP_TEST]
        if dropped:
            logger.info(
                f"6️⃣  تست HTTP واقعی روی {len(candidates)} کانفیگ سریع‌تر "
                f"({dropped} تست نشد — سقف MAX_HTTP_TEST)"
            )
        else:
            logger.info("6️⃣  تست HTTP واقعی...")

        configs_ms, s6 = await http_test_batch(candidates)
        s6["not_tested"] = dropped
        stats["layer6_http"] = s6
        logger.info(f"   → {len(configs_ms)}\n")
        final = [
            vless.add_tag(c, ms, country_map.get(c, "")) for c, ms in configs_ms
        ]

    # سریع‌ترین‌ها اول
    final.sort(key=vless.get_latency_ms)

    logger.info(
        f"\n{'=' * 55}\n✅ Pipeline کامل\n"
        f"   {s1['total']} → {s1['valid']} → {s2['unique']} → "
        f"{s3['connected']} → {s4['passed']} → {s5['passed']} → {len(final)}\n"
        f"{'=' * 55}"
    )
    return final, stats


async def main() -> None:
    started = time.monotonic()
    logger.info("🎯 VPN Collector v2")
    logger.info(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if SKIP_XRAY:
        logger.info("⚙️  SKIP_XRAY فعاله — لایه ۶ اجرا نمیشه")

    raw: List[str] = []
    valid: List[str] = []
    pipe_stats: Dict = {}
    error: str = ""

    try:
        raw = await collect()
        valid, pipe_stats = await pipeline(raw)

        if valid:
            save(valid, VALID_FILE)
        else:
            # فایل قبلی رو دست نمی‌زنیم: بهتره کاربر کانفیگ کهنه داشته باشه
            # تا لینک subscription خالی.
            logger.warning(
                "⚠️ هیچ کانفیگی pipeline رو پاس نکرد — valid.txt قبلی حفظ شد"
            )
    except KeyboardInterrupt:
        logger.info("⛔ متوقف")
        return
    except Exception as exc:
        error = str(exc)
        logger.error(f"❌ {exc}", exc_info=True)
    finally:
        elapsed = time.monotonic() - started
        full_stats = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "duration_seconds": round(elapsed, 1),
            "raw_collected": len(raw),
            "valid_configs": len(valid),
            "skip_xray": SKIP_XRAY,
            "pipeline": pipe_stats,
        }
        if error:
            full_stats["error"] = error[:300]
        save_stats(full_stats)

    if SKIP_TELEGRAM:
        logger.info("⏭️  تلگرام رد شد")
    else:
        publisher = Publisher()
        if error:
            await publisher.send_error(error)
        elif valid:
            await publisher.publish(valid, full_stats)
        else:
            logger.warning("⚠️ هیچ کانفیگ معتبری نماند!")
            if await publisher.connect():
                await publisher.send(
                    "⚠️ *هشدار*\nهیچ کانفیگ از pipeline رد نشد!\n"
                    f"🕐 {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
                )

    logger.info(f"\n🎉 {elapsed:.1f}s | {len(valid)} کانفیگ معتبر")
    if error:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
