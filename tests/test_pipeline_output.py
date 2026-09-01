"""تست خروجی نهایی pipeline — لایه ۶ نباید کانفیگ سالم را دور بریزد."""
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
TLS_MS = {cfg(1): 10.0, cfg(2): 20.0, cfg(3): 30.0, cfg(4): 40.0}


def _stub_layers(monkeypatch, http_result):
    """لایه‌های ۱ تا ۵ را بدون شبکه رد می‌کند و لایه ۶ را جایگزین می‌کند."""
    n = len(CFGS)
    monkeypatch.setattr(
        main, "filter_by_format",
        lambda c: (list(c), {"total": n, "valid": n, "invalid": 0}),
    )
    monkeypatch.setattr(
        main, "deduplicate",
        lambda c: (list(c), {"total": n, "unique": n}),
    )

    async def tcp(c):
        return list(c), {"total": n, "connected": n, "failed": 0}

    async def tls(c):
        return [(x, TLS_MS[x]) for x in c], {"total": n, "passed": n, "failed": 0}

    async def geo(c):
        return [(x, "US") for x in c], {"total": n, "passed": n, "failed": 0}

    async def http(c):
        return http_result(c)

    monkeypatch.setattr(main, "test_tcp_batch", tcp)
    monkeypatch.setattr(main, "test_tls_batch", tls)
    monkeypatch.setattr(main, "check_geo_batch", geo)
    monkeypatch.setattr(main, "http_test_batch", http)


def test_untested_configs_are_kept_after_verified(monkeypatch):
    """باگ: با سقف MAX_HTTP_TEST خروجی از ۱۸۰۰ به ۲۹۰ می‌افتاد."""
    def http_result(candidates):
        # فقط اولی از دو کاندید تست‌شده پاس می‌شود، با تأخیر بالا
        return [(candidates[0], 900.0)], {"total": 2, "passed": 1, "failed": 1}

    _stub_layers(monkeypatch, http_result)
    monkeypatch.setattr(main, "SKIP_XRAY", False)
    monkeypatch.setattr(main, "MAX_HTTP_TEST", 2)

    final, stats = asyncio.run(main.pipeline(CFGS))

    # ۱ تأییدشده + ۲ تست‌نشده (کاندید دومی که HTTP نداد حذف می‌شود)
    assert len(final) == 3
    assert stats["layer6_http"]["not_tested"] == 2
    # تأییدشده اول است، هرچند تأخیرش بیشتر است
    assert vless.get_latency_ms(final[0]) == 900.0
    assert [vless.get_latency_ms(c) for c in final[1:]] == [30.0, 40.0]
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
    assert stats["layer6_http"]["not_tested"] == 0
    assert [vless.get_latency_ms(c) for c in final] == [100.0, 101.0, 102.0, 103.0]


def test_skip_xray_keeps_all_sorted_by_tls(monkeypatch):
    _stub_layers(monkeypatch, lambda c: ([], {}))
    monkeypatch.setattr(main, "SKIP_XRAY", True)

    final, stats = asyncio.run(main.pipeline(list(reversed(CFGS))))
    assert len(final) == 4
    assert stats["layer6_http"]["skipped"] is True
    assert [vless.get_latency_ms(c) for c in final] == [10.0, 20.0, 30.0, 40.0]


def test_save_writes_lf_only(tmp_path, monkeypatch):
    """روی ویندوز خروجی CRLF می‌شد و بعضی کلاینت‌ها آن را نمی‌خواندند."""
    path = tmp_path / "valid.txt"
    monkeypatch.setattr(main, "CONFIGS_DIR", str(tmp_path))
    main.save([cfg(1), cfg(2)], str(path))
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.count(b"\n") == 2


def test_save_empty_list_writes_empty_file(tmp_path, monkeypatch):
    path = tmp_path / "valid.txt"
    monkeypatch.setattr(main, "CONFIGS_DIR", str(tmp_path))
    main.save([], str(path))
    assert path.read_bytes() == b""
