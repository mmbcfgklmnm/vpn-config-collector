"""تست خروجی نهایی pipeline ۷ لایه — لایه ۷ نباید کانفیگ سالم را دور بریزد."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import main, vless

UUID = "11111111-2222-3333-4444-555555555555"


def cfg(n: int) -> str:
    return f"vless://{UUID}@10.0.0.{n}:443?security=tls&type=tcp#node{n}"


# چهار کانفیگ با تأخیر TLS صعودی
CFGS = [cfg(1), cfg(2), cfg(3), cfg(4)]
TCP_MS = {c: 5.0 for c in CFGS}
TLS_MS = {cfg(1): 10.0, cfg(2): 20.0, cfg(3): 30.0, cfg(4): 40.0}
ALL_IRAN = {c: 150.0 + 10 * i for i, c in enumerate(CFGS)}
# حالت واقعی‌تر: فقط بخشی از پول از نودهای ایرانی جواب می‌دهد.
SOME_IRAN = {cfg(1): 180.0, cfg(2): 220.0}


def _stub_layers(monkeypatch, http_result, iran_ms=None):
    """لایه‌های شبکه‌ای را بدون شبکه جایگزین می‌کند.

    هر چهار لایه‌ی شبکه‌ای باید stub شوند؛ اگر check_iran_batch جا بماند تست
    واقعاً به check-host.net وصل می‌شود و کند/شکننده می‌شود.
    """
    n = len(CFGS)
    iran_ms = ALL_IRAN if iran_ms is None else iran_ms
    monkeypatch.setattr(
        main, "filter_by_format",
        lambda c: (list(c), {"total": n, "valid": n, "invalid": 0, "cdn_plain": 0}),
    )
    monkeypatch.setattr(
        main, "deduplicate",
        lambda c: (list(c), {"total": n, "unique": n}),
    )

    async def tcp(c):
        return [(x, TCP_MS[x]) for x in c], {"total": n, "connected": n, "failed": 0}

    async def iran(c):
        kept = [(x, iran_ms[x]) for x in c if x in iran_ms]
        return kept, {
            "total": len(c), "passed": len(kept), "failed": len(c) - len(kept),
        }

    async def tls(c):
        return [(x, TLS_MS[x]) for x in c], {
            "total": len(c), "passed": len(c), "failed": 0,
        }

    async def geo(c):
        return [(x, "US") for x in c], {
            "total": len(c), "passed": len(c), "failed": 0,
        }

    async def http(c):
        return http_result(c)

    monkeypatch.setattr(main, "test_tcp_batch", tcp)
    monkeypatch.setattr(main, "check_iran_batch", iran)
    monkeypatch.setattr(main, "test_tls_batch", tls)
    monkeypatch.setattr(main, "check_geo_batch", geo)
    monkeypatch.setattr(main, "http_test_batch", http)


def test_only_http_verified_configs_are_published(monkeypatch):
    """تست‌نشده‌ها publish نمی‌شوند و فقط در آمار شمرده می‌شوند."""
    def http_result(candidates):
        # فقط اولی از دو کاندید تست‌شده پاس می‌شود، با تأخیر بالا
        return [(candidates[0], 900.0)], {"total": 2, "passed": 1, "failed": 1}

    _stub_layers(monkeypatch, http_result)
    monkeypatch.setattr(main, "SKIP_XRAY", False)
    monkeypatch.setattr(main, "MAX_HTTP_TEST", 2)

    final, stats = asyncio.run(main.pipeline(CFGS))

    assert len(final) == 1
    assert stats["layer7_http"]["not_tested"] == 2
    assert vless.get_latency_ms(final[0]) == 900.0
    assert vless.get_country(final[0]) == "US"


def test_no_cap_means_only_verified(monkeypatch):
    """وقتی سقف نمی‌خورد، خروجی همان تأییدشده‌هاست."""
    def http_result(candidates):
        return (
            [(c, 100.0 + i) for i, c in enumerate(candidates)],
            {"total": 4, "passed": 4, "failed": 0},
        )

    _stub_layers(monkeypatch, http_result)
    monkeypatch.setattr(main, "SKIP_XRAY", False)
    monkeypatch.setattr(main, "MAX_HTTP_TEST", 100)

    final, stats = asyncio.run(main.pipeline(CFGS))
    assert len(final) == 4
    assert stats["layer7_http"]["not_tested"] == 0
    assert [vless.get_latency_ms(c) for c in final] == [100.0, 101.0, 102.0, 103.0]


def test_skip_xray_keeps_all_sorted_by_tls(monkeypatch):
    _stub_layers(monkeypatch, lambda c: ([], {}))
    monkeypatch.setattr(main, "SKIP_XRAY", True)

    final, stats = asyncio.run(main.pipeline(list(reversed(CFGS))))
    assert len(final) == 4
    assert stats["layer7_http"]["skipped"] is True
    assert [vless.get_latency_ms(c) for c in final] == [10.0, 20.0, 30.0, 40.0]


# ─── لایه ۴: دسترسی از ایران ──────────────────────────────

def test_iran_layer_shrinks_pool(monkeypatch):
    """کانفیگی که از نودهای ایرانی جواب نداد به لایه‌های بعد نمی‌رسد."""
    _stub_layers(
        monkeypatch,
        lambda c: ([(x, 300.0) for x in c], {"total": len(c), "passed": len(c)}),
        iran_ms=SOME_IRAN,
    )
    monkeypatch.setattr(main, "SKIP_XRAY", False)
    monkeypatch.setattr(main, "MAX_HTTP_TEST", 100)

    final, stats = asyncio.run(main.pipeline(CFGS))
    assert len(final) == 2
    assert stats["layer4_iran"]["passed"] == 2
    assert stats["layer5_tls"]["total"] == 2
    assert all(vless.is_iran_verified(c) for c in final)
    assert stats["summary"]["iran_verified"] == 2


def test_iran_latency_is_tagged_on_config(monkeypatch):
    """برچسب IR همراه خود لینک سفر می‌کند — ربات به فایل دوم وابسته نیست."""
    _stub_layers(
        monkeypatch, lambda c: ([(x, 300.0) for x in c], {"total": len(c)}),
        iran_ms={cfg(1): 187.0},
    )
    monkeypatch.setattr(main, "SKIP_XRAY", False)
    monkeypatch.setattr(main, "MAX_HTTP_TEST", 100)

    final, _ = asyncio.run(main.pipeline(CFGS))
    assert vless.get_iran_ms(final[0]) == 187.0


def test_funnel_has_one_entry_per_layer(monkeypatch):
    """funnel = ورودی + شش لایه‌ی فیلترکننده + خروجی نهایی."""
    _stub_layers(
        monkeypatch, lambda c: ([(x, 300.0) for x in c], {"total": len(c)}),
    )
    monkeypatch.setattr(main, "SKIP_XRAY", False)
    monkeypatch.setattr(main, "MAX_HTTP_TEST", 100)

    _, stats = asyncio.run(main.pipeline(CFGS))
    assert len(stats["summary"]["funnel"]) == 8
    assert stats["summary"]["funnel"][0] == len(CFGS)


def test_empty_after_format_returns_early(monkeypatch):
    """هیچ لایه‌ی شبکه‌ای نباید روی پول خالی صدا شود."""
    monkeypatch.setattr(
        main, "filter_by_format",
        lambda c: ([], {"total": len(c), "valid": 0, "invalid": len(c)}),
    )

    async def boom(c):
        raise AssertionError("لایه‌ی شبکه‌ای نباید اجرا شود")

    monkeypatch.setattr(main, "test_tcp_batch", boom)
    final, stats = asyncio.run(main.pipeline(CFGS))
    assert final == []
    assert set(stats) == {"layer1_format"}
