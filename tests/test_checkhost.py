"""تست لایه ۴ — دسترسی از ایران با API check-host.net.

هیچ‌کدام از این تست‌ها به شبکه وصل نمی‌شوند؛ پاسخ‌های API شبیه‌سازی می‌شوند.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tester import checkhost_tester as ch

UUID = "11111111-2222-3333-4444-555555555555"


def cfg(host: str, port: int = 443, name: str = "n") -> str:
    return f"vless://{UUID}@{host}:{port}?security=tls&type=ws&path=%2Fw#{name}"


# ─── تفسیر پاسخ API ───────────────────────────────────────
# قرارداد API: [{"time":..}] موفق، [{"error":..}] ناموفق، null یعنی هنوز
# تمام نشده. تشخیص ندادن null باعث می‌شد نتیجه‌ی نیمه‌کاره «مرده» خوانده شود.

def test_read_result_pending_node_is_not_done():
    payload = {"ir1.node.check-host.net": None,
               "ir3.node.check-host.net": [{"time": 0.2, "address": "1.2.3.4"}]}
    assert ch._read_result(payload) == (False, 0, 0.0)


def test_read_result_counts_successful_nodes_and_min_latency():
    payload = {
        "ir1.node.check-host.net": [{"time": 0.412, "address": "1.2.3.4"}],
        "ir3.node.check-host.net": [{"time": 0.180, "address": "1.2.3.4"}],
        "ir5.node.check-host.net": [{"error": "Connection timed out"}],
    }
    done, nodes_ok, ms = ch._read_result(payload)
    assert (done, nodes_ok) == (True, 2)
    assert ms == 180.0          # ثانیه → میلی‌ثانیه، کمترین مقدار


def test_read_result_all_errors_is_done_with_zero_nodes():
    payload = {"ir1.node.check-host.net": [{"error": "timeout"}]}
    assert ch._read_result(payload) == (True, 0, 0.0)


def test_read_result_rejects_empty_and_wrong_types():
    assert ch._read_result({}) == (False, 0, 0.0)
    assert ch._read_result(None) == (False, 0, 0.0)
    assert ch._read_result("nope") == (False, 0, 0.0)


def test_read_result_ignores_unparsable_time():
    payload = {"ir1.node.check-host.net": [{"time": "زیاد"}]}
    assert ch._read_result(payload) == (True, 0, 0.0)


# ─── endpoint ─────────────────────────────────────────────

def test_endpoint_of():
    assert ch.endpoint_of(cfg("104.17.5.5", 8080)) == "104.17.5.5:8080"
    assert ch.endpoint_of("garbage") == ""


def test_is_ipv6_detects_bracketed_and_bare():
    assert ch._is_ipv6("[2001:db8::1]") is True
    assert ch._is_ipv6("2001:db8::1") is True
    assert ch._is_ipv6("1.2.3.4") is False


# ─── سهمیه ────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def test_budget_zero_target_means_no_limit():
    async def go():
        budget = ch._Budget(0)
        await budget.hit()
        return budget.satisfied

    assert _run(go()) is False


def test_budget_satisfied_at_target():
    async def go():
        budget = ch._Budget(2)
        await budget.hit()
        first = budget.satisfied
        await budget.hit()
        return first, budget.satisfied

    assert _run(go()) == (False, True)


# ─── یک endpoint ──────────────────────────────────────────


def test_check_endpoint_reports_submit_error(monkeypatch):
    async def boom(session, endpoint):
        raise RuntimeError("HTTP 429")

    monkeypatch.setattr(ch, "_submit", boom)
    nodes, ms, reason = _run(ch.check_endpoint(None, "1.2.3.4:443"))
    assert (nodes, ms) == (0, 0.0)
    assert reason.startswith("submit:")


def test_check_endpoint_without_request_id(monkeypatch):
    async def none(session, endpoint):
        return None

    monkeypatch.setattr(ch, "_submit", none)
    assert _run(ch.check_endpoint(None, "1.2.3.4:443"))[2] == "request_id نداد"


def test_check_endpoint_blocked_from_iran(monkeypatch):
    async def submit(session, endpoint):
        return "rid"

    async def poll(session, request_id):
        return 0, 0.0

    monkeypatch.setattr(ch, "_submit", submit)
    monkeypatch.setattr(ch, "_poll", poll)
    assert _run(ch.check_endpoint(None, "1.2.3.4:443")) == (0, 0.0, "از ایران بسته است")


def test_check_endpoint_alive(monkeypatch):
    async def submit(session, endpoint):
        return "rid"

    async def poll(session, request_id):
        return 2, 187.0

    monkeypatch.setattr(ch, "_submit", submit)
    monkeypatch.setattr(ch, "_poll", poll)
    assert _run(ch.check_endpoint(None, "1.2.3.4:443")) == (2, 187.0, "")


# ─── دسته ─────────────────────────────────────────────────

def _stub_check(monkeypatch, table, counter=None):
    """table: endpoint → (nodes_ok, ms, reason)."""
    async def fake(session, endpoint):
        if counter is not None:
            counter.append(endpoint)
        return table.get(endpoint, (0, 0.0, "از ایران بسته است"))

    monkeypatch.setattr(ch, "check_endpoint", fake)
    monkeypatch.setattr(ch, "SKIP_CHECKHOST", False)
    monkeypatch.setattr(ch, "CHECKHOST_MIN_NODES", 1)
    monkeypatch.setattr(ch, "CHECKHOST_TARGET_ALIVE", 0)
    monkeypatch.setattr(ch, "CHECKHOST_MAX_ENDPOINTS", 0)
    monkeypatch.setattr(ch, "CHECKHOST_CONCURRENCY", 4)


def test_batch_skip_passes_everything_through(monkeypatch):
    monkeypatch.setattr(ch, "SKIP_CHECKHOST", True)
    configs = [cfg("1.1.1.1"), cfg("2.2.2.2")]
    valid, stats = _run(ch.check_iran_batch(configs))
    assert [c for c, _ in valid] == configs
    assert stats["skipped"] is True
    assert stats["passed"] == 2


def test_batch_tests_each_endpoint_once(monkeypatch):
    """سه کانفیگ روی یک endpoint = یک تست، ولی هر سه در خروجی."""
    same = [cfg("1.2.3.4", 443, f"n{i}") for i in range(3)]
    seen = []
    _stub_check(monkeypatch, {"1.2.3.4:443": (2, 150.0, "")}, seen)

    valid, stats = _run(ch.check_iran_batch(same))
    assert [c for c, _ in valid] == same
    assert {ms for _, ms in valid} == {150.0}
    assert seen == ["1.2.3.4:443"]
    assert (stats["endpoints_total"], stats["endpoints_alive"]) == (1, 1)
    assert (stats["passed"], stats["failed"]) == (3, 0)


def test_batch_drops_blocked_endpoints(monkeypatch):
    alive, dead = cfg("1.1.1.1"), cfg("2.2.2.2")
    _stub_check(monkeypatch, {"1.1.1.1:443": (1, 90.0, "")})
    valid, stats = _run(ch.check_iran_batch([alive, dead]))
    assert valid == [(alive, 90.0)]
    assert (stats["passed"], stats["failed"]) == (1, 1)
    assert stats["top_reasons"] == {"از ایران بسته است": 1}


def test_batch_requires_min_nodes(monkeypatch):
    """یک نود ایرانی وقتی MIN_NODES=2 است کافی نیست."""
    one_node = cfg("1.1.1.1")
    _stub_check(monkeypatch, {"1.1.1.1:443": (1, 90.0, "")})
    monkeypatch.setattr(ch, "CHECKHOST_MIN_NODES", 2)
    valid, stats = _run(ch.check_iran_batch([one_node]))
    assert valid == []
    assert stats["endpoints_alive"] == 0


def test_batch_excludes_ipv6_endpoints(monkeypatch):
    """host:port برای IPv6 مبهم است؛ به check-host فرستاده نمی‌شود."""
    v6, v4 = cfg("[2001:db8::1]", 8443), cfg("1.1.1.1")
    seen = []
    _stub_check(monkeypatch, {"1.1.1.1:443": (1, 30.0, "")}, seen)
    valid, stats = _run(ch.check_iran_batch([v6, v4]))
    assert [c for c, _ in valid] == [v4]
    assert seen == ["1.1.1.1:443"]
    assert stats["endpoints_total"] == 1


def test_batch_sorts_by_iran_latency(monkeypatch):
    slow, fast = cfg("1.1.1.1"), cfg("2.2.2.2")
    _stub_check(monkeypatch, {"1.1.1.1:443": (1, 300.0, ""),
                              "2.2.2.2:443": (1, 120.0, "")})
    valid, _ = _run(ch.check_iran_batch([slow, fast]))
    assert [c for c, _ in valid] == [fast, slow]


# ─── صرفه‌جویی در سهمیه ───────────────────────────────────

def test_batch_stops_at_target_alive(monkeypatch):
    """به محض رسیدن به هدف، endpoint بعدی تست نمی‌شود."""
    hosts = [f"{i}.{i}.{i}.{i}" for i in (1, 2, 3)]
    seen = []
    _stub_check(monkeypatch, {f"{h}:443": (1, 50.0, "") for h in hosts}, seen)
    monkeypatch.setattr(ch, "CHECKHOST_TARGET_ALIVE", 1)
    monkeypatch.setattr(ch, "CHECKHOST_CONCURRENCY", 1)

    valid, stats = _run(ch.check_iran_batch([cfg(h) for h in hosts]))
    assert len(seen) == 1
    assert stats["endpoints_checked"] == 1
    assert len(valid) == 1


def test_batch_respects_max_endpoints(monkeypatch):
    hosts = [f"{i}.{i}.{i}.{i}" for i in (1, 2, 3)]
    seen = []
    _stub_check(monkeypatch, {f"{h}:443": (1, 50.0, "") for h in hosts}, seen)
    monkeypatch.setattr(ch, "CHECKHOST_MAX_ENDPOINTS", 2)
    _run(ch.check_iran_batch([cfg(h) for h in hosts]))
    assert len(seen) == 2


def test_batch_checks_cdn_endpoints_first(monkeypatch):
    """با سهمیه‌ی محدود، IP کلودفلر باید جلوتر از VPS خام تست شود."""
    vps, cf = cfg("1.2.3.4"), cfg("104.17.5.5", 8080)
    seen = []
    _stub_check(monkeypatch, {"104.17.5.5:8080": (1, 40.0, ""),
                              "1.2.3.4:443": (1, 40.0, "")}, seen)
    monkeypatch.setattr(ch, "CHECKHOST_TARGET_ALIVE", 1)
    monkeypatch.setattr(ch, "CHECKHOST_CONCURRENCY", 1)

    valid, _ = _run(ch.check_iran_batch([vps, cf]))   # VPS اول در ورودی
    assert seen == ["104.17.5.5:8080"]
    assert [c for c, _ in valid] == [cf]


# ─── fail-open ────────────────────────────────────────────
# اگر خودِ check-host از کار بیفتد، «هیچ‌کدام زنده نیست» نتیجه‌گیری غلطی است؛
# در آن حالت این لایه کنار گذاشته می‌شود تا خروجی صفر نشود.

def test_batch_fails_open_when_api_errors_dominate(monkeypatch):
    configs = [cfg("1.1.1.1"), cfg("2.2.2.2")]
    _stub_check(monkeypatch, {ep: (0, 0.0, "submit: HTTP 429")
                              for ep in ("1.1.1.1:443", "2.2.2.2:443")})
    valid, stats = _run(ch.check_iran_batch(configs))
    assert [c for c, _ in valid] == configs
    assert [ms for _, ms in valid] == [0.0, 0.0]
    assert stats["skipped"] is True
    assert stats["fail_open"] is True
    assert (stats["passed"], stats["api_errors"]) == (2, 2)


def test_batch_does_not_fail_open_when_api_is_healthy(monkeypatch):
    """همه بسته ولی API سالم → خروجی خالی است، نه fail-open."""
    _stub_check(monkeypatch, {})
    valid, stats = _run(ch.check_iran_batch([cfg("1.1.1.1"), cfg("2.2.2.2")]))
    assert valid == []
    assert stats["api_errors"] == 0
    assert stats["skipped"] is False
    assert "fail_open" not in stats


def test_batch_does_not_fail_open_when_something_passed(monkeypatch):
    """یک endpoint زنده یعنی API کار می‌کند؛ پس بقیه واقعاً بسته‌اند."""
    ok, broken = cfg("1.1.1.1"), cfg("2.2.2.2")
    _stub_check(monkeypatch, {"1.1.1.1:443": (1, 70.0, ""),
                              "2.2.2.2:443": (0, 0.0, "submit: HTTP 500")})
    valid, stats = _run(ch.check_iran_batch([ok, broken]))
    assert [c for c, _ in valid] == [ok]
    assert stats["api_errors"] == 1
    assert "fail_open" not in stats


def test_batch_empty_input(monkeypatch):
    _stub_check(monkeypatch, {})
    valid, stats = _run(ch.check_iran_batch([]))
    assert valid == []
    assert (stats["total"], stats["endpoints_total"]) == (0, 0)



