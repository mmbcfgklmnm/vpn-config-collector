"""تست لایه ۶: کشور به‌ازای هر IP یکتا، نه هر کانفیگ.

شکایت صریح کاربر: «چرا انتخاب کشور فقط آمریکا است؟ کشورهای بیشتری اضافه کن.»
ریشه‌اش این‌جا بود: اجرای واقعی ۱۷:۴۶ کشورِ ۴۷۵۴ از ۶۶۰۳ کانفیگ را «نامعلوم»
گذاشت، چون هر کانفیگ خودش resolve و کوئری می‌کرد و ۶۶۰۰ کوئریِ همزمان سهمیه‌ی
هر سه API را می‌سوزاند. این‌جا سه چیز قفل می‌شود:

  ۱. یک کوئری برای هر IP یکتا (نه هر کانفیگ).
  ۲. rate-limit باعث کوریِ کل اجرا نشود — اول صبر، بعد کنار گذاشتن.
  ۳. «نامعلوم» همان‌طور که بود پاس شود ولی برچسب کشور نگیرد.

بدون شبکه و بدون DNS اجرا می‌شود.
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tester import geo_checker as geo

UUID = "11111111-2222-3333-4444-555555555555"


def cfg(host: str, name: str = "n") -> str:
    return f"vless://{UUID}@{host}:443?security=tls&type=tcp#{name}"


@pytest.fixture(autouse=True)
def _clean_module_state():
    """حالت ماژول بین تست‌ها نشت نکند — cache و API های کنارگذاشته‌شده."""
    geo._geo_cache.clear()
    geo._disabled_apis.clear()
    geo._api_429.clear()
    yield
    geo._geo_cache.clear()
    geo._disabled_apis.clear()
    geo._api_429.clear()


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status = status
        self._payload = payload if payload is not None else []
        self.headers = headers or {}

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """جای aiohttp.ClientSession — هر POST یک جواب آماده از صف."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.posted = []          # بدنه‌ی هر POST، برای بررسی اندازه‌ی دسته

    def post(self, url, data=None, **kwargs):
        self.posted.append(json.loads(data))
        return self.responses.pop(0) if self.responses else FakeResponse(500)

    def get(self, url, **kwargs):
        return self.responses.pop(0) if self.responses else FakeResponse(500)


def batch_ok(pairs, **headers):
    return FakeResponse(
        200,
        [{"status": "success", "query": ip, "countryCode": cc} for ip, cc in pairs],
        headers or {"X-Rl": "14", "X-Ttl": "60"},
    )


# ─── تصمیم، بدون شبکه ─────────────────────────────────────

def test_unresolvable_host_is_rejected():
    assert geo.judge(None, "")[0] is False


def test_iran_prefix_is_rejected_without_an_api_call():
    ok, country, reason = geo.judge("2.144.0.1", "")
    assert (ok, country) == (False, "IR")
    assert "IP ایران" in reason


def test_blocked_country_is_rejected():
    ok, country, _ = geo.judge("8.8.8.8", "KP")
    assert (ok, country) == (False, "KP")


def test_unknown_country_passes_but_gets_no_label():
    """یک قطعیِ API نباید کل خروجی را صفر کند؛ ولی ادعای کشور هم نمی‌کنیم."""
    assert geo.judge("8.8.8.8", "") == (True, geo.UNKNOWN, "")


def test_known_country_passes_with_its_code():
    assert geo.judge("8.8.8.8", "NL") == (True, "NL", "")


def test_country_names_normalize_to_codes():
    assert geo.normalize_country("Iran") == "IR"
    assert geo.normalize_country("nl") == "NL"
    assert geo.normalize_country("Netherlands") == ""   # نه "NE"
    assert geo.normalize_country(None) == ""


# ─── دسته‌ای پرسیدن ───────────────────────────────────────

def test_batch_asks_once_per_hundred_ips():
    ips = [f"9.9.{n // 256}.{n % 256}" for n in range(250)]
    session = FakeSession([
        batch_ok([(ip, "US") for ip in ips[:100]]),
        batch_ok([(ip, "DE") for ip in ips[100:200]]),
        batch_ok([(ip, "NL") for ip in ips[200:]]),
    ])
    found = asyncio.run(geo.lookup_batch(ips, session))
    assert [len(body) for body in session.posted] == [100, 100, 50]
    assert len(found) == 250
    assert found[ips[0]] == "US" and found[ips[-1]] == "NL"


def test_batch_skips_entries_the_api_marked_failed():
    session = FakeSession([FakeResponse(200, [
        {"status": "success", "query": "8.8.8.8", "countryCode": "US"},
        {"status": "fail", "query": "10.0.0.1", "message": "private range"},
    ])])
    found = asyncio.run(geo.lookup_batch(["8.8.8.8", "10.0.0.1"], session))
    assert found == {"8.8.8.8": "US"}


def test_batch_result_is_cached_so_a_second_call_is_free():
    session = FakeSession([batch_ok([("8.8.8.8", "US")])])
    asyncio.run(geo.lookup_batch(["8.8.8.8"], session))
    again = FakeSession([])
    assert asyncio.run(geo.lookup_countries(["8.8.8.8"], again)) == {"8.8.8.8": "US"}
    assert again.posted == []


def test_one_rate_limit_does_not_blind_the_whole_run(monkeypatch):
    """۴۲۹ اول = صبر و تلاش دوباره. نسخه‌ی قبلی API را برای همیشه دور می‌ریخت."""
    monkeypatch.setattr(geo.asyncio, "sleep", _no_sleep)
    session = FakeSession([
        FakeResponse(429, [], {"X-Ttl": "1"}),
        batch_ok([("8.8.8.8", "US")]),
    ])
    found = asyncio.run(geo.lookup_batch(["8.8.8.8"], session))
    assert found == {"8.8.8.8": "US"}
    assert geo.GEO_BATCH_API not in geo._disabled_apis


def test_repeated_rate_limits_finally_stop_the_batch(monkeypatch):
    monkeypatch.setattr(geo.asyncio, "sleep", _no_sleep)
    session = FakeSession([FakeResponse(429, [], {"X-Ttl": "1"})] * 5)
    found = asyncio.run(geo.lookup_batch(["8.8.8.8"], session))
    assert found == {}
    assert geo.GEO_BATCH_API in geo._disabled_apis
    assert len(session.posted) == geo.GEO_MAX_429


def test_exhausted_quota_waits_before_the_next_post(monkeypatch):
    """X-Rl صفر یعنی «هیچ درخواستی نفرست» — نادیده گرفتنش یک ساعت بن است."""
    waited = []

    async def spy(seconds):
        waited.append(seconds)

    monkeypatch.setattr(geo.asyncio, "sleep", spy)
    ips = [f"9.9.9.{n}" for n in range(150)]
    session = FakeSession([
        batch_ok([(ip, "US") for ip in ips[:100]], **{"X-Rl": "0", "X-Ttl": "7"}),
        batch_ok([(ip, "US") for ip in ips[100:]], **{"X-Rl": "13", "X-Ttl": "60"}),
    ])
    asyncio.run(geo.lookup_batch(ips, session))
    assert waited == [7.0]


def test_wait_is_capped_and_tolerates_a_junk_header():
    assert geo._wait_for("60") == 60.0
    assert geo._wait_for("99999") == geo.GEO_WAIT_CAP_SEC
    assert geo._wait_for(None) == 5.0
    assert geo._wait_for("abc") == 5.0


def test_expired_budget_sends_nothing():
    """احترام به X-Ttl می‌تواند دقیقه‌ها صبر باشد؛ بودجه سقفِ آن است."""
    import time as _time
    session = FakeSession([batch_ok([("8.8.8.8", "US")])])
    found = asyncio.run(
        geo.lookup_batch(["8.8.8.8"], session, deadline=_time.monotonic() - 1)
    )
    assert (found, session.posted) == ({}, [])


def test_single_ip_fallback_is_bounded(monkeypatch):
    """اگر batch بمیرد، هزار درخواستِ تک‌به‌تک همان rate-limit را می‌سازد.

    پس پشتیبان تا یک دسته تلاش می‌کند و بقیه «نامعلوم» می‌مانند — عددِ حدسی
    ننوشتن، بهتر از عددِ غلط نوشتن است.
    """
    monkeypatch.setattr(geo, "GEO_BATCH_SIZE", 2)
    asked = []

    async def fake_single(ip, session):
        asked.append(ip)
        return "NL"

    async def dead_batch(ips, session, deadline=0.0):
        return {}

    monkeypatch.setattr(geo, "get_country", fake_single)
    monkeypatch.setattr(geo, "lookup_batch", dead_batch)
    found = asyncio.run(
        geo.lookup_countries([f"9.9.9.{n}" for n in range(9)], FakeSession([]))
    )
    assert len(asked) == 2 and len(found) == 2


async def _no_sleep(_seconds):
    return None


# ─── مسیر کامل لایه ۶ ─────────────────────────────────────

def test_geo_queries_each_unique_ip_once_not_each_config(monkeypatch):
    """قلبِ اشکال: ۶ کانفیگ روی ۲ میزبان = ۲ resolve و یک دسته با ۲ IP.

    نسخه‌ی قبلی ۶ بار resolve و ۶ کوئری می‌زد؛ در مقیاس واقعی همین الگو
    ۶۶۰۰ کوئریِ همزمان می‌ساخت و سهمیه‌ی API را می‌سوزاند.
    """
    hosts = {"a.example.com": "203.0.113.5", "b.example.com": "198.51.100.7"}
    resolved = []
    asked = []

    async def fake_resolve(host):
        resolved.append(host)
        return hosts.get(host)

    async def fake_lookup(ips, session, deadline=0.0):
        asked.append(sorted(ips))
        return {"203.0.113.5": "NL", "198.51.100.7": "SE"}

    monkeypatch.setattr(geo, "resolve_host", fake_resolve)
    monkeypatch.setattr(geo, "lookup_countries", fake_lookup)

    configs = [cfg(h, f"n{i}") for i in range(3) for h in hosts]
    valid, stats = asyncio.run(geo.check_geo_batch(configs))

    assert sorted(resolved) == sorted(hosts)          # هر میزبان یک بار
    assert asked == [sorted(hosts.values())]          # یک کوئری، دو IP
    assert stats["unique_hosts"] == 2 and stats["unique_ips"] == 2
    assert stats["countries"] == {"NL": 3, "SE": 3}
    assert len(valid) == 6


def test_iran_ips_never_reach_the_api(monkeypatch):
    """رد سریع با پیشوند، قبل از خرج کردن سهمیه."""
    asked = []

    async def fake_resolve(host):
        return "2.144.0.9" if host.startswith("ir") else "203.0.113.5"

    async def fake_lookup(ips, session, deadline=0.0):
        asked.append(list(ips))
        return {"203.0.113.5": "NL"}

    monkeypatch.setattr(geo, "resolve_host", fake_resolve)
    monkeypatch.setattr(geo, "lookup_countries", fake_lookup)

    valid, stats = asyncio.run(
        geo.check_geo_batch([cfg("ir.example.com"), cfg("ok.example.com")])
    )
    assert asked == [["203.0.113.5"]]
    assert [c for c, _ in valid] == [cfg("ok.example.com")]
    assert stats["fail_reasons"] == {"IP ایران": 1}


def test_unresolvable_and_hostless_configs_are_reported_apart(monkeypatch):
    async def fake_resolve(host):
        return None

    async def fake_lookup(ips, session, deadline=0.0):
        return {}

    monkeypatch.setattr(geo, "resolve_host", fake_resolve)
    monkeypatch.setattr(geo, "lookup_countries", fake_lookup)

    valid, stats = asyncio.run(geo.check_geo_batch([cfg("dead.example.com"), "junk"]))
    assert valid == []
    assert stats["fail_reasons"] == {"resolve ناموفق": 1, "host خالی": 1}


def test_api_outage_still_passes_configs_as_unknown(monkeypatch):
    """قاعده‌ی پروژه: «تست نشد» ≠ «رد شد» — قطعیِ API خروجی را صفر نمی‌کند."""
    async def fake_resolve(host):
        return "203.0.113.5"

    async def fake_lookup(ips, session, deadline=0.0):
        return {}

    monkeypatch.setattr(geo, "resolve_host", fake_resolve)
    monkeypatch.setattr(geo, "lookup_countries", fake_lookup)

    valid, stats = asyncio.run(geo.check_geo_batch([cfg("x.example.com")]))
    assert [country for _, country in valid] == [geo.UNKNOWN]
    assert stats["unknown_country"] == 1
    assert stats["passed"] == 1
