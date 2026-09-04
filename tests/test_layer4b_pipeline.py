"""تست جای لایه ۴b در pipeline — احیا نه پول را باد می‌کند و نه funnel را.

سه چیز این‌جا قفل می‌شود:
  • فقط کانفیگ‌های *ردشده‌ی* لایه ۴ نامزد احیا هستند؛ به سالم‌ها دست نمی‌خورد.
  • کانفیگ احیاشده تا از ایران تأیید نشود منتشر نمی‌شود (حدس منتشر نمی‌کنیم).
  • funnel هشت عضو و نزولی می‌ماند، وگرنه نمودار README می‌شکند.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import clean_ip, main, vless
from src.clean_ip import REVIVE_MARK


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    """دفترِ نوبتِ لایه ۷ در تست به فایل واقعی مخزن نگاه نکند.

    pipeline فقط می‌خواندش (نوشتن کارِ main است)، ولی همان خواندن هم نتیجه را
    به «این ماشین دیروز چه آزمود» گره می‌زد.
    """
    monkeypatch.setattr(main, "LAYER7_LOG_FILE", str(tmp_path / "layer7.txt"))

UUID = "11111111-2222-3333-4444-555555555555"
CLEAN = "172.67.1.2"

# روی CF و از CDN رد می‌شود → نامزد احیا
CDN_CFG = (
    f"vless://{UUID}@104.16.5.9:443?type=ws&security=tls"
    "&host=cdn.example.com&path=%2Fws#cdn"
)
# سرور مستقیم → حتی اگر بسته باشد، احیا معنا ندارد
DIRECT_CFG = f"vless://{UUID}@10.0.0.7:443?type=tcp&security=tls#direct"

CFGS = [CDN_CFG, DIRECT_CFG]


def _stub(monkeypatch, iran, clean_ips=(CLEAN,)):
    """همه‌ی لایه‌های شبکه‌ای + منبع IP تمیز را جایگزین می‌کند.

    `iran` یک تابع است: کانفیگ → تأخیر از ایران (۰ = بسته). هم برای پول اصلی
    و هم برای داوری دوباره‌ی کانفیگ‌های احیاشده به کار می‌رود، چون در خود
    pipeline هم همان یک تابع است.
    """
    n = len(CFGS)
    monkeypatch.setattr(
        main, "filter_by_format",
        lambda c: (list(c), {"total": n, "valid": len(c), "invalid": 0}),
    )
    monkeypatch.setattr(main, "deduplicate", lambda c: (list(c), {"unique": len(c)}))

    async def tcp(c):
        return [(x, 5.0) for x in c], {"connected": len(c)}

    async def iran_batch(c):
        kept = [(x, iran(x)) for x in c if iran(x) > 0]
        return kept, {"total": len(c), "passed": len(kept)}

    async def tls(c):
        return [(x, 20.0) for x in c], {"passed": len(c)}

    async def geo(c):
        return [(x, "US") for x in c], {"passed": len(c)}

    async def http(c):
        return [(x, 100.0) for x in c], {"total": len(c), "passed": len(c)}

    async def find(want=6, verify=None, path=""):
        return list(clean_ips)

    monkeypatch.setattr(main, "test_tcp_batch", tcp)
    monkeypatch.setattr(main, "check_iran_batch", iran_batch)
    monkeypatch.setattr(main, "test_tls_batch", tls)
    monkeypatch.setattr(main, "check_geo_batch", geo)
    monkeypatch.setattr(main, "http_test_batch", http)
    monkeypatch.setattr(main, "SKIP_XRAY", False)
    monkeypatch.setattr(main, "MAX_HTTP_TEST", 100)
    monkeypatch.setattr(main, "CF_CLEAN_IP_ENABLED", True)
    monkeypatch.setattr(clean_ip, "find_clean_ips", find)
    # فایل کش واقعی پروژه در تست دست نمی‌خورد.
    monkeypatch.setattr(clean_ip, "remember", lambda ips, path=None: None)


def test_healthy_configs_are_never_touched(monkeypatch):
    """CDN سالم است و مستقیمِ بسته نامزد نیست → احیا اجرا نمی‌شود."""
    _stub(monkeypatch, lambda c: 180.0 if c == CDN_CFG else 0.0)
    final, stats = asyncio.run(main.pipeline(CFGS))
    assert [vless.get_name(c) for c in final] == ["cdn"]
    assert REVIVE_MARK not in final[0]
    assert stats["layer4b_revive"]["blocked"] == 1
    assert stats["layer4b_revive"]["candidates"] == 0


def test_a_blocked_cdn_config_comes_back_on_a_clean_ip(monkeypatch):
    """هر دو کانفیگ بسته‌اند؛ فقط CDN با IP تمیز برمی‌گردد."""
    _stub(monkeypatch, lambda c: 205.0 if REVIVE_MARK in c else 0.0)
    final, stats = asyncio.run(main.pipeline(CFGS))

    assert len(final) == 1
    info = vless.parse(final[0])
    assert info.host == CLEAN                        # آدرس عوض شد
    assert info.params["host"] == "cdn.example.com"  # مسیر CDN دست‌نخورده
    assert info.params["sni"] == "cdn.example.com"
    assert info.params["path"] == "/ws"
    assert vless.get_iran_ms(final[0]) == 205.0      # از ایران تأیید شد
    assert stats["layer4b_revive"]["passed"] == 1
    assert stats["summary"]["revived"] == 1
    # funnel هشت عضو و نزولی
    funnel = stats["summary"]["funnel"]
    assert len(funnel) == 8
    assert funnel == sorted(funnel, reverse=True)


def test_a_revived_config_that_iran_still_blocks_is_dropped(monkeypatch):
    """احیا یک حدس است؛ حدسِ تأییدنشده منتشر نمی‌شود."""
    _stub(monkeypatch, lambda c: 0.0)
    final, stats = asyncio.run(main.pipeline(CFGS))
    assert final == []
    assert stats["layer4b_revive"]["revived"] == 1
    assert stats["layer4b_revive"]["passed"] == 0
    assert "layer5_tls" not in stats          # پول خالی، لایه‌ی بعدی صدا نشد


def test_no_clean_ip_means_no_revival(monkeypatch):
    _stub(monkeypatch, lambda c: 0.0, clean_ips=())
    final, stats = asyncio.run(main.pipeline(CFGS))
    assert final == []
    assert stats["layer4b_revive"]["clean_ips"] == 0
    assert "revived" not in stats["layer4b_revive"]


def test_the_layer_can_be_switched_off(monkeypatch):
    _stub(monkeypatch, lambda c: 0.0)
    monkeypatch.setattr(main, "CF_CLEAN_IP_ENABLED", False)

    def boom(*args, **kwargs):
        raise AssertionError("با CF_CLEAN_IP_ENABLED=0 نباید IP تمیز بخواهد")

    monkeypatch.setattr(clean_ip, "find_clean_ips", boom)
    final, stats = asyncio.run(main.pipeline(CFGS))
    assert final == []
    assert stats["layer4b_revive"] == {
        "enabled": False, "blocked": 0, "candidates": 0,
    }


# ─── سنجه‌های لایه ۷ روی برچسب ────────────────────────────

def test_quality_metrics_reach_the_tag(monkeypatch):
    """افت/لرزش/سرعت از کانال کناری _quality به برچسب می‌رسند و در آمار نمی‌مانند."""
    _stub(monkeypatch, lambda c: 180.0 if c == CDN_CFG else 0.0)

    async def http(c):
        return (
            [(x, 100.0) for x in c],
            {
                "total": len(c),
                "_quality": {
                    x: {"loss_pct": 0.0, "jitter_ms": 9.0, "speed_kbps": 430.0}
                    for x in c
                },
            },
        )

    monkeypatch.setattr(main, "http_test_batch", http)
    final, stats = asyncio.run(main.pipeline(CFGS))
    assert vless.get_loss_pct(final[0]) == 0.0
    assert vless.get_jitter_ms(final[0]) == 9.0
    assert vless.get_speed_kbps(final[0]) == 430.0
    assert "_quality" not in stats["layer7_http"]
    assert stats["summary"]["stable"] == 1
    assert stats["summary"]["avg_speed_kbps"] == 430.0


def test_zero_loss_outranks_a_faster_but_lossy_node(monkeypatch):
    """خواسته‌ی صریح کاربر: ۱۰۰ms با ۰٪ افت از ۵۰ms با ۲۰٪ افت ارزشمندتر است."""
    _stub(monkeypatch, lambda c: 180.0)
    latency = {CDN_CFG: 100.0, DIRECT_CFG: 50.0}

    async def http(c):
        return (
            [(x, latency[x]) for x in c],
            {"_quality": {
                CDN_CFG: {"loss_pct": 0.0, "jitter_ms": 5.0, "speed_kbps": 400.0},
                DIRECT_CFG: {
                    "loss_pct": 20.0, "jitter_ms": 40.0, "speed_kbps": 900.0
                },
            }},
        )

    monkeypatch.setattr(main, "http_test_batch", http)
    final, _ = asyncio.run(main.pipeline(CFGS))
    assert [vless.get_name(c) for c in final] == ["cdn", "direct"]


def test_untested_configs_make_no_stability_claim(monkeypatch):
    """پول ذخیره برچسب P/J/S نمی‌گیرد: «۰٪ افت» بدون اندازه‌گیری دروغ است."""
    _stub(monkeypatch, lambda c: 180.0)
    monkeypatch.setattr(main, "MAX_HTTP_TEST", 1)
    _, stats = asyncio.run(main.pipeline(CFGS))
    pool = stats["_reserve"]
    assert len(pool) == 1
    assert vless.get_loss_pct(pool[0]) == -1.0
    assert vless.get_speed_kbps(pool[0]) == 0.0
