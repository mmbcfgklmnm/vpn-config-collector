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
۴b احیای کلودفلری با IP تمیز → ۵ TLS → ۶ Geo →
۷ تأخیر واقعی + پایداری + سرعت با xray در چند دور

TCP قبل از check-host است چون محلی و ارزان است و سهمیه‌ی check-host را
روی سرورهای مرده هدر نمی‌دهد. TLS/Geo بعد از check-host هستند چون آن‌جا
پول کوچک شده و هزینه‌شان ناچیز است.

لایه ۴b شماره‌ی جدا ندارد چون صافی نیست: کانفیگ‌هایی که لایه ۴ «بسته»
اعلام کرد و روی CDN هستند را با IP تمیز کلودفلر برمی‌گرداند و نتیجه را
دوباره از ایران می‌سنجد. پس هیچ‌وقت پول را بزرگ‌تر از ورودی لایه ۴
نمی‌کند و funnel نزولی می‌ماند.

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

from src import clean_ip, outputs, vless
from src.config import (
    CF_CLEAN_IP_ENABLED, CONFIGS_DIR, LAYER7_LOG_FILE, MAX_HTTP_TEST, POOL_MAX,
    PUBLISH_AFTER_COLLECT, SKIP_TELEGRAM, SKIP_XRAY, STATS_FILE,
)
from src.logger import get_logger
from src.publisher.publisher import Publisher
from src.scraper.github_scraper import scrape_github
from src.scraper.telegram_scraper import scrape_telegram
from src.scraper.web_scraper import scrape_web
from src.tester import ledger
from src.tester.checkhost_tester import check_iran_batch, iran_latency
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


async def _verify_clean_ips(ips: List[str]) -> Dict[str, float]:
    """داوری IP های نامزد از نودهای ایرانی → {ip: ms}.

    check-host روی «host:port» کار می‌کند، پس IP ها به endpoint تبدیل و
    جواب دوباره به IP برگردانده می‌شود. پورت مرجع SCAN_PORT است چون IP های
    انی‌کست CF روی همه‌ی پورت‌های مجموعه‌شان یک‌جور رفتار می‌کنند.
    """
    judged = await iran_latency([f"{ip}:{clean_ip.SCAN_PORT}" for ip in ips])
    return {
        endpoint.rsplit(":", 1)[0]: ms for endpoint, ms in judged.items()
    }


async def revive_blocked(
    before: List[str], survivors: List[str]
) -> Tuple[List[Tuple[str, float]], Dict]:
    """لایه ۴b — کانفیگ کلودفلری با ورودیِ فیلترشده را با IP تمیز برمی‌گرداند.

    فقط روی کانفیگ‌هایی اجرا می‌شود که لایه ۴ حکم «از ایران بسته است» داده؛
    به کانفیگ سالم دست نمی‌زند (عوض کردن آدرسِ چیزی که کار می‌کند فقط ریسک
    است). Host و SNI دست‌نخورده می‌مانند تا مسیر CDN حفظ شود و فقط آدرسِ
    ورودی عوض می‌شود.

    خروجی دوباره از check-host رد می‌شود و فقط چیزی که از ایران جواب داد
    برمی‌گردد: کانفیگ احیاشده یک *حدس* است و حدس منتشر نمی‌شود.

    شمارنده‌ی funnel دست نمی‌خورد. این یک مرحله‌ی جبرانی داخل لایه ۴ است نه
    صافیِ تازه؛ اضافه کردنش به funnel نمودار README را می‌شکست.
    """
    stats: Dict = {"enabled": CF_CLEAN_IP_ENABLED, "blocked": 0, "candidates": 0}
    if not CF_CLEAN_IP_ENABLED:
        return [], stats

    kept = set(survivors)
    blocked = [c for c in before if c not in kept]
    candidates = [c for c in blocked if clean_ip.can_revive(c)]
    stats.update({"blocked": len(blocked), "candidates": len(candidates)})
    if not candidates:
        return [], stats

    logger.info(
        f"4️⃣ب  احیای CDN — {len(candidates)} نامزد از {len(blocked)} ورودی بسته"
    )
    ips = await clean_ip.find_clean_ips(verify=_verify_clean_ips)
    stats["clean_ips"] = len(ips)
    if not ips:
        logger.warning("   IP تمیزی تأیید نشد — احیا انجام نشد\n")
        return [], stats

    revived = clean_ip.revive_batch(candidates, ips)
    stats["revived"] = len(revived)
    if not revived:
        return [], stats

    rechecked, s4b = await check_iran_batch(revived)
    # ms > 0 یعنی check-host واقعاً از ایران وصل شد. بی‌حکم‌ها (ms == 0) کنار
    # گذاشته می‌شوند: برای کانفیگ اصلی «بی‌حکم» یعنی شک، ولی برای کانفیگی که
    # خودمان ساخته‌ایم یعنی هیچ دلیلی برای انتشارش نداریم.
    alive = [(cfg, ms) for cfg, ms in rechecked if ms > 0]
    stats["passed"] = len(alive)
    stats["recheck"] = s4b
    clean_ip.remember([clean_ip.ip_of(cfg) for cfg, _ in alive])
    logger.info(f"   → {len(alive)} کانفیگ احیا شد\n")
    return alive, stats


async def pipeline(configs: List[str]) -> Tuple[List[str], Dict]:
    logger.info("\n🔬 Pipeline ۷ لایه")
    logger.info(f"   ورودی: {len(configs)}\n")
    stats: Dict = {}
    counts: List[int] = [len(configs)]
    # پول ذخیره: کانفیگ‌هایی که لایه ۶ را پاس کردند ولی لایه ۷ تأییدشان نکرد
    # چون *تست نشدند* (سقف MAX_HTTP_TEST یا SKIP_XRAY). فقط برای پر کردن
    # سهمیه‌ی ۱۰تایی کانال به کار می‌رود، با برچسب صریح «تست‌نشده».
    reserve: List[str] = []

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
    before_iran = list(configs)
    configs_iran, s4 = await check_iran_batch(configs)
    stats["layer4_iran"] = s4
    iran_ms_map = dict(configs_iran)
    configs = [c for c, _ in configs_iran]

    # لایه ۴b قبل از شمارش funnel است تا نمودار نزولی بماند: احیاشده‌ها
    # زیرمجموعه‌ی همان چیزی هستند که لایه ۴ رد کرد، نه ورودی تازه.
    revived, s4b = await revive_blocked(before_iran, configs)
    if s4b:
        stats["layer4b_revive"] = s4b
    for cfg, ms in revived:
        iran_ms_map[cfg] = ms
        configs.append(cfg)

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

    # سنجه‌های لایه ۷ (افت بسته، لرزش، سرعت) — از کانال کناری _quality پر
    # می‌شود. تا آن‌جا خالی می‌ماند، پس کانفیگ تست‌نشده برچسب پایداری نمی‌گیرد:
    # «۰٪ افت» ادعای بزرگی است و بدون اندازه‌گیری نوشتنش دروغ است.
    quality: Dict[str, dict] = {}

    def tag(cfg: str, latency: float) -> str:
        q = quality.get(cfg) or {}
        return vless.add_tag(
            cfg, latency, country_map.get(cfg, ""), iran_ms_map.get(cfg, 0.0),
            loss_pct=q.get("loss_pct", -1.0),
            jitter_ms=q.get("jitter_ms", -1.0),
            speed_kbps=q.get("speed_kbps", 0.0),
        )

    if SKIP_XRAY:
        logger.info("7️⃣  HTTP رد شد (SKIP_XRAY)")
        stats["layer7_http"] = {
            "total": len(configs), "passed": len(configs),
            "skipped": True, "rounds": 0, "avg_ms": 0,
        }
        final = [tag(c, tls_ms_map.get(c) or tcp_ms_map.get(c, 0)) for c in configs]
    else:
        # xray سنگین‌ترین لایه است؛ ورودی‌اش هم با سقفِ تعداد (MAX_HTTP_TEST) و
        # هم با بودجه‌ی زمانی بریده می‌شود. کانفیگ تست‌نشده publish نمی‌شود:
        # لایه‌های ۳ و ۵ فقط می‌گویند «چیزی روی این پورت جواب می‌دهد»، لایه ۷
        # می‌گوید «تونل VLESS واقعاً کار می‌کند».
        # ترتیبِ ارزش: تأییدشده‌های ایران اول، بعد سریع‌ترین TLS — اگر سقف
        # بخورد، کانفیگی که از ایران جواب داده نباید جای خود را به یک سرورِ
        # سریعِ بسته بدهد.
        ordered = sorted(
            configs,
            key=lambda c: (
                0.0 if iran_ms_map.get(c, 0.0) > 0 else 1.0,
                tls_ms_map.get(c, float("inf")),
            ),
        )
        # ...ولی ترتیبِ ارزش به‌تنهایی یعنی همان سریع‌ترین‌ها هر نیم‌ساعت
        # آزموده می‌شوند و کشفِ تازه هیچ‌وقت به تونل نمی‌رسد — دقیقاً شکایت
        # کاربر. دفتر نوبت سهمِ تضمینی به آزموده‌نشده‌ها می‌دهد و صف را
        # درهم می‌بافد تا بودجه هرجا قطع شود نسبت حفظ شده باشد.
        log = ledger.load(LAYER7_LOG_FILE)
        turn = ledger.plan(ordered, log, MAX_HTTP_TEST)
        candidates = turn.queue
        not_tested = len(turn.spare)
        logger.info(
            f"7️⃣  صفِ تونل: {len(candidates)} کانفیگ "
            f"({turn.fresh_queued} برای اولین بار، "
            f"{turn.proven_queued} تأییدشده‌ی اجرای قبل) | اجرا #{log.run}"
        )
        if not_tested or turn.held:
            logger.warning(
                f"   {not_tested} کانفیگ به سقف/بودجه خوردند و به پول ذخیره "
                f"می‌روند؛ {turn.held} تا هم اجرای قبل ردشان کرد و نوبت "
                "فرصت دوباره‌شان نرسیده (نه در صف، نه در ذخیره)"
            )

        configs_ms, s7 = await http_test_batch(candidates)
        # سنجه‌ها از stats بیرون کشیده می‌شوند تا در stats.json تکرار نشوند؛
        # جای واقعیِ آن‌ها برچسب خودِ کانفیگ است.
        quality.update(s7.pop("_quality", {}))
        # کانفیگ‌هایی که به بودجه‌ی زمانی لایه ۷ خوردند و نوبتشان نرسید.
        budget_skipped = s7.pop("_skipped", [])
        s7["not_tested"] = not_tested + len(budget_skipped)
        stats["layer7_http"] = s7

        # ثبت در دفتر: فقط چیزی که *واقعاً* آزموده شد. بی‌نوبت‌مانده‌ها ثبت
        # نمی‌شوند، وگرنه اجرای بعدی آن‌ها را «آزموده» می‌بیند و همان حلقه‌ی
        # تکرار از نو ساخته می‌شود.
        waiting = set(budget_skipped)
        s7["rotation"] = log.record(
            [c for c in candidates if c not in waiting],
            {c for c, _ in configs_ms},
        )
        s7["rotation"]["held_after_fail"] = turn.held
        s7["rotation"]["run"] = log.run
        # نوشتنِ دفتر مثل بقیه‌ی خروجی‌ها کارِ main() است؛ pipeline فقط حساب
        # می‌کند. همان الگوی _reserve.
        stats["_layer7_log"] = log

        logger.info(f"   → {len(configs_ms)}\n")
        final = [tag(c, ms) for c, ms in configs_ms]
        # ذخیره فقط از *تست‌نشده‌ها* پر می‌شود، نه از رد‌شده‌های لایه ۷.
        # قاعده‌ی همیشگی پروژه: «تست نشد» ≠ «رد شد». کانفیگی که xray واقعاً
        # امتحان کرد و کار نکرد، حق ورود به کانال ندارد؛ کانفیگی که فقط به
        # سقف زمان خورد، هنوز بهترین گزینه‌ی موجود برای پر کردن سهمیه است.
        # بی‌نوبت‌های بودجه اول می‌آیند: آن‌ها سریع‌ترین‌های صف بودند.
        reserve = [
            tag(c, tls_ms_map.get(c) or tcp_ms_map.get(c, 0.0))
            for c in budget_skipped + turn.spare
        ]

    reserve = reserve[:POOL_MAX]

    # ترتیب نهایی: پایداری قبل از سرعتِ خام — خواسته‌ی صریح کاربر. «نودی با
    # پینگ ۱۰۰ms و ۰٪ افت از نودی با پینگ ۵۰ms و ۲۰٪ افت ارزشمندتر است.»
    # نامعلوم وسط می‌نشیند: نه پاداشِ ادعای نکرده می‌گیرد، نه جریمه‌ی افتی
    # که اندازه‌گیری نشده.
    def rank(cfg: str) -> Tuple[float, float]:
        loss = vless.get_loss_pct(cfg)
        bucket = 0.0 if loss == 0 else (1.0 if loss < 0 else 2.0)
        return bucket, vless.get_latency_ms(cfg)

    final.sort(key=rank)
    counts.append(len(final))
    iran_verified = sum(1 for c in final if vless.is_iran_verified(c))
    speeds = [s for s in (vless.get_speed_kbps(c) for c in final) if s > 0]
    # چرخشِ نوبت لایه ۷ در آمار هم می‌آید: «این اجرا چند کانفیگ را برای اولین
    # بار آزمود و چند تای تازه تأیید شد» تنها پاسخِ قابل‌اندازه‌گیری به شکایتِ
    # «ربات دیگر کانفیگ تازه پیدا نمی‌کند» است. SKIP_XRAY کلیدی ندارد و صفر
    # می‌ماند — «اندازه‌گیری نشد» با «صفرِ اندازه‌گیری‌شده» قاطی نمی‌شود.
    rotation = (stats.get("layer7_http") or {}).get("rotation") or {}
    stats["summary"] = {
        "funnel": counts,
        "final": len(final),
        "iran_verified": iran_verified,
        "reserve": len(reserve),
        "revived": stats.get("layer4b_revive", {}).get("passed", 0),
        "stable": sum(1 for c in final if vless.is_stable(c)),
        "speed_measured": len(speeds),
        "avg_speed_kbps": round(sum(speeds) / len(speeds), 1) if speeds else 0.0,
        "fresh_tested": rotation.get("first_time", 0),
        "new_passed": rotation.get("new_passed", 0),
    }
    # کانال پشتیِ برگرداندن ذخیره بدون شکستن قرارداد دوعضوی این تابع.
    # main() فوراً pop می‌کند تا در stats.json ننشیند (چند صد لینک VLESS
    # داخل فایل آمار نه خوانا است نه لازم).
    stats["_reserve"] = reserve

    logger.info(
        f"\n{'=' * 55}\n✅ Pipeline کامل\n"
        f"   {' → '.join(str(n) for n in counts)}\n"
        f"   🇮🇷 تأییدشده از ایران: {iran_verified}/{len(final)}\n"
        f"   💚 بدون افت بسته: {stats['summary']['stable']}/{len(final)}"
        + (
            f" | ⚡ میانگین سرعت: {stats['summary']['avg_speed_kbps']} KB/s"
            if speeds else ""
        )
        + (
            f"\n   ♻️  احیاشده با IP تمیز: {stats['summary']['revived']}"
            if stats["summary"]["revived"] else ""
        )
        + f"\n   🗃️  پول ذخیره (تست‌نشده): {len(reserve)}"
        + (
            f"\n   🆕 اولین‌بار آزموده شد: {rotation.get('first_time', 0)}"
            f" | تازه تأیید شد: {rotation.get('new_passed', 0)}"
            f" | برگشته: {rotation.get('recovered', 0)}"
            if rotation else ""
        )
        + f"\n{'=' * 55}"
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
    reserve: List[str] = []
    pipe_stats: Dict = {}
    written: Dict = {}
    error: str = ""
    elapsed = 0.0

    try:
        raw = await collect()
        valid, pipe_stats = await pipeline(raw)
        # ذخیره از آمار جدا می‌شود تا فایل stats.json فهرست لینک نشود.
        reserve = list(pipe_stats.pop("_reserve", []))
        # دفترِ نوبتِ لایه ۷ (اگر آن لایه اجرا شده باشد). همین‌جا نوشته می‌شود
        # نه بعد از خروجی‌ها: آزمونِ تونل واقعاً انجام شده و حافظه باید همان
        # را بگوید حتی اگر نوشتن فایل‌ها بعداً خطا بدهد. pop لازم است چون
        # آبجکت است و json.dump رویش می‌شکند.
        turn_log = pipe_stats.pop("_layer7_log", None)
        if turn_log is not None:
            ledger.save(turn_log, LAYER7_LOG_FILE)
        # خروجی‌ها فقط وقتی چیزی هست نوشته میشن؛ وگرنه فایل قبلی می‌ماند تا
        # لینک subscription کاربران با یک اجرای ناموفق از کار نیفتد.
        written = outputs.write_all(
            valid, {"pipeline": pipe_stats}, raw, pool_configs=reserve,
        )
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
            "reserve_pool": len(reserve),
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
