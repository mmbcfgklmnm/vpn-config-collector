"""تست لایه ۳ (TCP) — فیلتر سختی که کاربر صریحاً خواست.

پس‌زمینه: کاربر ۲۴۸ کانفیگ خروجی را دستی تست کرد و فقط ۸۸ تا TCP فعال
داشتند. دو تغییر: تست per-endpoint (نه per-config) و لزوم چند اتصال موفق.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tester import tcp_tester

UUID = "11111111-2222-3333-4444-555555555555"


def cfg(host: str, port: int = 443, name: str = "n") -> str:
    return f"vless://{UUID}@{host}:{port}?security=tls&type=tcp#{name}"


class FakeConnect:
    """جای tcp_connect: نتیجه‌ی هر تلاش را از یک لیست برمی‌دارد."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    async def __call__(self, host, port):
        self.calls += 1
        if self.results:
            return self.results.pop(0)
        return False, 0.0


def _patch(monkeypatch, fake, attempts=2, gap=0.0):
    monkeypatch.setattr(tcp_tester, "tcp_connect", fake)
    monkeypatch.setattr(tcp_tester, "TCP_ATTEMPTS", attempts)
    monkeypatch.setattr(tcp_tester, "TCP_ATTEMPT_GAP_SEC", gap)


# ─── endpoint ─────────────────────────────────────────────

def test_endpoint_of_groups_by_host_port():
    assert tcp_tester.endpoint_of(cfg("1.2.3.4", 443)) == "1.2.3.4:443"
    assert tcp_tester.endpoint_of("garbage") == ""


def test_endpoint_of_handles_ipv6():
    ep = tcp_tester.endpoint_of(cfg("[2001:db8::1]", 8443))
    assert ep == "2001:db8::1:8443"
    host, _, port = ep.rpartition(":")
    assert (host, port) == ("2001:db8::1", "8443")


# ─── چند تلاش ─────────────────────────────────────────────

def test_probe_needs_all_attempts(monkeypatch):
    """یک اتصال موفق کافی نیست — تلاش دوم هم باید جواب دهد."""
    fake = FakeConnect([(True, 30.0), (False, 0.0)])
    _patch(monkeypatch, fake)
    ok, ms = asyncio.run(tcp_tester.tcp_probe("1.2.3.4", 443))
    assert (ok, ms) == (False, 0.0)
    assert fake.calls == 2


def test_probe_stops_at_first_failure(monkeypatch):
    """شکست اول یعنی رد — تلاش‌های بعدی وقت تلف نمی‌کنند."""
    fake = FakeConnect([(False, 0.0), (True, 10.0), (True, 10.0)])
    _patch(monkeypatch, fake, attempts=3)
    assert asyncio.run(tcp_tester.tcp_probe("1.2.3.4", 443)) == (False, 0.0)
    assert fake.calls == 1


def test_probe_reports_best_latency(monkeypatch):
    fake = FakeConnect([(True, 80.0), (True, 25.0)])
    _patch(monkeypatch, fake)
    assert asyncio.run(tcp_tester.tcp_probe("1.2.3.4", 443)) == (True, 25.0)


def test_probe_single_attempt_mode(monkeypatch):
    fake = FakeConnect([(True, 42.0), (False, 0.0)])
    _patch(monkeypatch, fake, attempts=1)
    assert asyncio.run(tcp_tester.tcp_probe("1.2.3.4", 443)) == (True, 42.0)
    assert fake.calls == 1


def test_probe_zero_attempts_is_treated_as_one(monkeypatch):
    fake = FakeConnect([(True, 42.0)])
    _patch(monkeypatch, fake, attempts=0)
    assert asyncio.run(tcp_tester.tcp_probe("1.2.3.4", 443))[0] is True
    assert fake.calls == 1


# ─── دسته ─────────────────────────────────────────────────

def test_batch_tests_each_endpoint_once(monkeypatch):
    """سه کانفیگ روی یک endpoint = یک تست، ولی هر سه در خروجی."""
    same = [cfg("1.2.3.4", 443, f"n{i}") for i in range(3)]
    fake = FakeConnect([(True, 20.0), (True, 20.0)])
    _patch(monkeypatch, fake)

    valid, stats = asyncio.run(tcp_tester.test_tcp_batch(same))
    assert len(valid) == 3
    assert stats["endpoints_total"] == 1
    assert stats["endpoints_alive"] == 1
    assert fake.calls == 2          # دو تلاش، نه شش
    assert stats["attempts_required"] == 2


def test_batch_drops_dead_endpoints(monkeypatch):
    alive_cfg = cfg("1.1.1.1", 443, "alive")
    dead_cfg = cfg("2.2.2.2", 443, "dead")

    async def fake(host, port):
        return (host == "1.1.1.1", 15.0 if host == "1.1.1.1" else 0.0)

    _patch(monkeypatch, fake)
    valid, stats = asyncio.run(tcp_tester.test_tcp_batch([alive_cfg, dead_cfg]))
    assert [c for c, _ in valid] == [alive_cfg]
    assert stats["connected"] == 1
    assert stats["failed"] == 1


def test_batch_counts_unparsable(monkeypatch):
    _patch(monkeypatch, FakeConnect([]))
    valid, stats = asyncio.run(tcp_tester.test_tcp_batch(["garbage", "vmess://x"]))
    assert valid == []
    assert stats["unparsable"] == 2
    assert stats["endpoints_total"] == 0


def test_batch_sorts_by_latency(monkeypatch):
    slow = cfg("1.1.1.1", 443, "slow")
    fast = cfg("2.2.2.2", 443, "fast")

    async def fake(host, port):
        return True, 200.0 if host == "1.1.1.1" else 20.0

    _patch(monkeypatch, fake)
    valid, _ = asyncio.run(tcp_tester.test_tcp_batch([slow, fast]))
    assert [c for c, _ in valid] == [fast, slow]


def test_batch_empty_input(monkeypatch):
    _patch(monkeypatch, FakeConnect([]))
    valid, stats = asyncio.run(tcp_tester.test_tcp_batch([]))
    assert valid == []
    assert stats["total"] == 0
