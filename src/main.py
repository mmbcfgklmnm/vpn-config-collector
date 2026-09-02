"""
VPN Config Collector v2 — Pipeline ۷ لایه
هدف: کمیت پایین، کیفیت بالا — و مهم‌تر: «قابل استفاده از ایران»، نه فقط «زنده».

تغییر بزرگ این نسخه
───────────────────
همه‌ی تست‌های محلی (TCP/TLS/xray) روی رانر گیت‌هاب در آمریکا اجرا می‌شوند.
اندازه‌گیری روی ۳۰ endpoint از خروجی تأییدشده‌ی خودِ پروژه:

    از نودهای آلمان/آمریکا/هلند :  ۳۰ از ۳۰ زنده
    از نودهای ایران (تهران/شیراز):   ۱ از ۳۰ زنده

یعنی pipeline «سالم» را درست می‌سنجید و «قابل استفاده» را نه. لایه ۴
(check-host از نودهای ایرانی) همین شکاف را می‌بندد.

ترتیب لایه‌ها و دلیلش
─────────────────────
۱ فرمت → ۲ حذف تکراری → ۳ TCP (فیلتر سخت) → ۴ دسترسی از ایران →
۵ TLS → ۶ Geo → ۷ HTTP واقعی با xray در چند دور

TCP قبل از check-host است چون محلی و ارزان است و سهمیه‌ی check-host را
روی سرورهای مرده هدر نمی‌دهد. TLS/Geo بعد از check-host هستند چون آن‌جا
پول کوچک شده و هزینه‌شان ناچیز است.

اصلاحات نسخه‌های قبلی که حفظ شده‌اند:
  • SKIP_* از config خوانده میشن (نه os.getenv مستقیم).
  • ورودی لایه‌ی xray با MAX_HTTP_TEST محدود میشه تا job با سقف ۵۵ دقیقه
    نیمه‌کاره کشته نشه.
  • اگه pipeline صفر کانفیگ داد، فایل‌های قبلی *پاک نمیشن*.
  • آمار حتی در صورت خطا نوشته میشه.
  • فقط کانفیگ تأییدشده publish میشه.
  • فایل‌ها با newline="\\n" نوشته میشن.
"""
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import outputs, vless
from src.config import (
    CONFIGS_DIR, MAX_HTTP_TEST, PUBLISH_AFTER_COLLECT, SKIP_TELEGRAM,
    SKIP_XRAY, STATS_FILE,
)
from src.logger import get_logger
from src.publisher.publisher import Publisher
from src.scraper.github_scraper import scrape_github
from src.scraper.telegram_scraper import scrape_telegram
from src.scraper.web_scraper import scrape_web
from src.tester.checkhost_tester import check_iran_batch
from src.tester.deduplicator import deduplicate
from src.tester.format_validator import filter_by_format
from src.tester.geo_checker import check_geo_batch
from src.tester.http_tester import http_test_batch
from src.tester.tcp_tester import test_tcp_batch
from src.tester.tls_tester import test_tls_batch

logger = get_logger("main")


def save_stats(stats: Dict) -> None:
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    with open(STATS_FILE, "w", encoding="utf-8", newline="\n") as fh:
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
    return unique


async def pipeline(configs: List[str]) -> Tuple[List[str], Dict]:
    logger.info("\n🔬 Pipeline ۷ لایه")
    logger.info(f"   ورودی: {len(configs)}\n")
    stats: Dict = {}
    counts: List[int] = [len(configs)]

    logger.info("1️⃣  فرمت + فیلتر VLESS...")
    configs, s1 = filter_by_format(configs)
    stats["layer1_format"] = s1
    counts.append(len(configs))
    logger.info(f"   → {len(configs)}\n")
    if not configs:
        return [], stats

    logger.info("2️⃣  حذف تکراری...")
    configs, s2 = deduplicate(configs)
    stats["layer2_dedup"] = s2
    counts.append(len(configs))
    logger.info(f"   → {len(configs)}\n")
    if not configs:
        return [], stats

    # فیلتر سخت و بی‌استثنا — درخواست صریح کاربر. با هیچ SKIP_* رد نمی‌شود.
    logger.info("3️⃣  تست TCP (فیلتر سخت)...")
    configs_tcp, s3 = await test_tcp_batch(configs)
    stats["layer3_tcp"] = s3
    tcp_ms_map = dict(configs_tcp)
    configs = [c for c, _ in configs_tcp]
    counts.append(len(configs))
    logger.info(f"   → {len(configs)}\n")
    if not configs:
        return [], stats

    logger.info("4️⃣  دسترسی از ایران (check-host)...")
    configs_iran, s4 = await check_iran_batch(configs)
    stats["layer4_iran"] = s4
    iran_ms_map = dict(configs_iran)
    configs = [c for c, _ in configs_iran]
    counts.append(len(configs))
    logger.info(f"   → {len(configs)}\n")
    if not configs:
        return [], stats

    logger.info("5️⃣  تست TLS Handshake...")
    configs_tls, s5 = await test_tls_batch(configs)
    stats["layer5_tls"] = s5
    tls_ms_map = dict(configs_tls)
    configs = [c for c, _ in configs_tls]
    counts.append(len(configs))
    logger.info(f"   → {len(configs)}\n")
    if not configs:
        return [], stats

    logger.info("6️⃣  بررسی Geo...")
    configs_country, s6 = await check_geo_batch(configs)
    stats["layer6_geo"] = s6
    country_map = dict(configs_country)
    configs = [c for c, _ in configs_country]
    counts.append(len(configs))
    logger.info(f"   → {len(configs)}\n")
    if not configs:
        return [], stats

    def tag(cfg: str, latency: float) -> str:
        return vless.add_tag(
            cfg, latency, country_map.get(cfg, ""), iran_ms_map.get(cfg, 0.0)
        )

    if SKIP_XRAY:
        logger.info("7️⃣  HTTP رد شد (SKIP_XRAY)")
        stats["layer7_http"] = {
            "total": len(configs), "passed": len(configs),
            "skipped": True, "rounds": 0, "avg_ms": 0,
        }
        final = [tag(c, tls_ms_map.get(c) or tcp_ms_map.get(c, 0)) for c in configs]
    else:
        # xray سنگین‌ترین لایه است؛ سریع‌ترین‌ها تا سقف MAX_HTTP_TEST تست
        # می‌شوند. کانفیگ تست‌نشده publish نمی‌شود: لایه‌های ۳ و ۵ فقط
        # می‌گویند «چیزی روی این پورت جواب می‌دهد»، لایه ۷ می‌گوید
        # «تونل VLESS واقعاً کار می‌کند».
        # تأییدشده‌های ایران اول: اگر سقف بخورد، کانفیگی که از ایران جواب
        # داده نباید جای خود را به یک سرورِ سریعِ بسته بدهد.
        ordered = sorted(
            configs,
            key=lambda c: (
                0.0 if iran_ms_map.get(c, 0.0) > 0 else 1.0,
                tls_ms_map.get(c, float("inf")),
            ),
        )
        candidates = ordered[:MAX_HTTP_TEST]
        not_tested = len(ordered) - len(candidates)
        if not_tested:
            logger.warning(
                f"7️⃣  تست HTTP روی {len(candidates)} کانفیگ سریع‌تر — "
                f"{not_tested} تا به سقف MAX_HTTP_TEST خوردند و publish نمیشن"
            )
        else:
            logger.info("7️⃣  تست HTTP واقعی (چند دور)...")

        configs_ms, s7 = await http_test_batch(candidates)
        s7["not_tested"] = not_tested
        stats["layer7_http"] = s7
        logger.info(f"   → {len(configs_ms)}\n")
        final = [tag(c, ms) for c, ms in configs_ms]

    final.sort(key=vless.get_latency_ms)
    counts.append(len(final))
    iran_verified = sum(1 for c in final if vless.is_iran_verified(c))
    stats["summary"] = {
        "funnel": counts,
        "final": len(final),
        "iran_verified": iran_verified,
    }

    logger.info(
        f"\n{'=' * 55}\n✅ Pipeline کامل\n"
        f"   {' → '.join(str(n) for n in counts)}\n"
        f"   🇮🇷 تأییدشده از ایران: {iran_verified}/{len(final)}\n"
        f"{'=' * 55}"
    )
    return final, stats


async def main() -> None:
    started = time.monotonic()
    logger.info("🎯 VPN Collector v2")
    logger.info(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if SKIP_XRAY:
        logger.info("⚙️  SKIP_XRAY فعاله — لایه ۷ اجرا نمیشه")

    raw: List[str] = []
    valid: List[str] = []
    pipe_stats: Dict = {}
    written: Dict = {}
    error: str = ""
    elapsed = 0.0

    try:
        raw = await collect()
        valid, pipe_stats = await pipeline(raw)
        # خروجی‌ها فقط وقتی چیزی هست نوشته میشن؛ وگرنه فایل قبلی می‌ماند تا
        # لینک subscription کاربران با یک اجرای ناموفق از کار نیفتد.
        written = outputs.write_all(valid, {"pipeline": pipe_stats}, raw)
        if not valid:
            logger.warning(
                "⚠️ هیچ کانفیگی pipeline رو پاس نکرد — فایل‌های قبلی حفظ شدند"
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
            "iran_verified": sum(1 for c in valid if vless.is_iran_verified(c)),
            "skip_xray": SKIP_XRAY,
            "written": written,
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
        elif not valid:
            logger.warning("⚠️ هیچ کانفیگ معتبری نماند!")
            if await publisher.connect():
                await publisher.send(
                    "⚠️ *هشدار*\nهیچ کانفیگ از pipeline رد نشد!\n"
                    f"🕐 {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
                )
        elif PUBLISH_AFTER_COLLECT:
            # فقط وقتی ربات همیشه-روشن نداری. وگرنه دو publisher با دو
            # حافظه‌ی چرخش مستقل، کانفیگ تکراری داخل cooldown پست می‌کنند.
            await publisher.publish(valid, full_stats)
        else:
            logger.info(
                "⏭️  انتشار در کانال به پروسه‌ی ربات واگذار شد "
                f"(PUBLISH_AFTER_COLLECT=0) — {len(valid)} کانفیگ آماده"
            )

    logger.info(f"\n🎉 {elapsed:.1f}s | {len(valid)} کانفیگ معتبر")
    if error:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
