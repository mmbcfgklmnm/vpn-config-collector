"""تست لایه ۴ — دسترسی از ایران با API check-host.net.

هیچ‌کدام از این تست‌ها به شبکه وصل نمی‌شوند؛ پاسخ‌های API شبیه‌سازی می‌شوند.
"""
import asyncio
import os
import sys
import time

import pytest

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


# ─── مطابقت با مستند رسمی API ─────────────────────────────
# نمونه‌های این بخش عیناً از مستند check-host.net برداشته شده‌اند.

DOC_TCP_RESULT = {
    "us1.node.check-host.net": [{"time": 0.03, "address": "104.28.31.42"}],
    "ch1.node.check-host.net": [{"error": "Connection timed out"}],
    "pt1.node.check-host.net": None,          # هنوز در حال تست
}


def test_doc_example_pending_node_blocks_completion():
    assert ch._read_result(DOC_TCP_RESULT) == (False, 0, 0.0)


def test_doc_example_read_when_pending_node_drops_out():
    payload = {k: v for k, v in DOC_TCP_RESULT.items() if v is not None}
    assert ch._read_result(payload) == (True, 1, 30.0)


def test_ping_shaped_nested_result_is_not_counted():
    """[[null]] و [[["OK", ..]]] فرمت ping است؛ برای tcp فقط dict معنا دارد."""
    payload = {
        "ir1.node.check-host.net": [[None]],
        "ir3.node.check-host.net": [[["OK", 0.044, "94.242.206.94"]]],
    }
    assert ch._read_result(payload) == (True, 0, 0.0)


def test_only_requested_nodes_are_counted():
    """اگر پاسخ نود اروپایی داشت، شمردنش یعنی سنجش از نقطه‌ی اشتباه."""
    payload = {
        "ir1.node.check-host.net": [{"error": "Connection timed out"}],
        "us1.node.check-host.net": [{"time": 0.03, "address": "1.2.3.4"}],
    }
    wanted = ["ir1.node.check-host.net", "ir3.node.check-host.net"]
    assert ch._read_result(payload, wanted) == (True, 0, 0.0)
    assert ch._read_result(payload)[1] == 1     # بدون فیلتر «زنده» خوانده می‌شد


def test_result_with_no_requested_node_is_not_done():
    payload = {"us1.node.check-host.net": [{"time": 0.03}]}
    assert ch._read_result(payload, ["ir1.node.check-host.net"]) == (False, 0, 0.0)



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


# ─── دروازه‌ی نرخ ─────────────────────────────────────────
# سهمیه‌ی API عمومی مستند نیست؛ دروازه از خودِ ۴۲۹ یاد می‌گیرد.

def test_gate_penalize_doubles_gap_up_to_cap():
    async def go():
        gate = ch._Gate(1.0, 3.0)
        await gate.penalize()
        first = gate.gap
        await gate.penalize()
        return first, gate.gap, gate.rate_limited

    assert _run(go()) == (2.0, 3.0, 2)


def test_gate_relax_returns_toward_floor(monkeypatch):
    monkeypatch.setattr(ch, "CHECKHOST_MIN_GAP_SEC", 1.0)

    async def go():
        gate = ch._Gate(1.0, 8.0)
        await gate.penalize()               # 2.0
        for _ in range(10):
            await gate.relax()
        return gate.gap

    assert _run(go()) == 1.0


def test_gate_spaces_requests():
    async def go():
        gate = ch._Gate(0.05, 1.0)
        start = time.monotonic()
        for _ in range(3):
            await gate.wait()
        return time.monotonic() - start

    assert _run(go()) >= 0.09


def test_gate_without_gap_does_not_wait():
    async def go():
        gate = ch._Gate(0.0, 0.0)
        start = time.monotonic()
        for _ in range(20):
            await gate.wait()
        return time.monotonic() - start

    assert _run(go()) < 0.5


# ─── ثبت درخواست ──────────────────────────────────────────

class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """فقط همان چیزی که _submit لازم دارد: get() به‌شکل context manager."""

    def __init__(self, payload, status=200):
        self._resp = _FakeResp(payload, status)
        self.urls = []

    def get(self, url, headers=None):
        self.urls.append(url)
        return self._resp


def test_submit_asks_for_every_configured_node(monkeypatch):
    monkeypatch.setattr(ch, "CHECKHOST_NODES",
                        ["ir1.node.check-host.net", "ir3.node.check-host.net"])
    session = _FakeSession({"ok": 1, "request_id": "29",
                            "permanent_link": "https://check-host.net/check-report/29"})
    assert _run(ch._submit(session, "1.2.3.4:443")) == "29"

    url = session.urls[0]
    assert url.count("node=") == 2
    assert "host=1.2.3.4:443" in url    # کولون نباید encode شود
    assert "max_nodes" not in url       # انتخاب نود کار ما است، نه سایت


def test_submit_raises_on_api_refusal():
    """ok=0 باید مثل خطای شبکه شمرده شود تا fail-open از کار نیفتد."""
    session = _FakeSession({"ok": 0, "error": "invalid host"})
    with pytest.raises(RuntimeError, match="invalid host"):
        _run(ch._submit(session, "1.2.3.4:443"))


def test_submit_raises_on_http_error():
    with pytest.raises(RuntimeError, match="500"):
        _run(ch._submit(_FakeSession({}, status=500), "1.2.3.4:443"))


def test_submit_raises_rate_limited_on_429():
    """۴۲۹ نوع خطای خودش را دارد تا دروازه‌ی نرخ عقب بکشد."""
    with pytest.raises(ch._RateLimited):
        _run(ch._submit(_FakeSession({}, status=429), "1.2.3.4:443"))


def test_submit_raises_without_request_id():
    with pytest.raises(RuntimeError):
        _run(ch._submit(_FakeSession({"ok": 1}), "1.2.3.4:443"))


# ─── انتظار برای نتیجه ────────────────────────────────────

IR3 = ("ir1.node.check-host.net", "ir3.node.check-host.net", "ir5.node.check-host.net")


def _fast_poll(monkeypatch, interval=0.0, timeout=1, min_nodes=2):
    monkeypatch.setattr(ch, "FIRST_POLL_DELAY", 0.0)
    monkeypatch.setattr(ch, "POLL_INTERVAL", interval)
    monkeypatch.setattr(ch, "CHECKHOST_TIMEOUT_SEC", timeout)
    monkeypatch.setattr(ch, "CHECKHOST_NODES", list(IR3))
    monkeypatch.setattr(ch, "CHECKHOST_MIN_NODES", min_nodes)


def test_poll_returns_as_soon_as_min_nodes_answered(monkeypatch):
    """حکم که قطعی شد، نودِ کند نباید تا انتهای مهلت معطل کند."""
    _fast_poll(monkeypatch)
    session = _FakeSession({
        "ir1.node.check-host.net": [{"time": 0.2}],
        "ir3.node.check-host.net": [{"time": 0.35}],
        "ir5.node.check-host.net": None,
    })
    assert _run(ch._poll(session, "rid")) == (True, 2, 200.0)
    assert len(session.urls) == 1


def test_poll_keeps_partial_result_until_deadline(monkeypatch):
    """یک نود موفق و MIN_NODES=2: تا مهلت صبر می‌کند، بعد همان را می‌دهد."""
    _fast_poll(monkeypatch, interval=0.02, timeout=0.1)
    session = _FakeSession({
        "ir1.node.check-host.net": [{"time": 0.2}],
        "ir3.node.check-host.net": None,
        "ir5.node.check-host.net": None,
    })
    assert _run(ch._poll(session, "rid")) == (True, 1, 200.0)
    assert len(session.urls) > 1


def test_poll_returns_finished_negative_result_at_once(monkeypatch):
    _fast_poll(monkeypatch)
    session = _FakeSession({node: [{"error": "Connection timed out"}] for node in IR3})
    assert _run(ch._poll(session, "rid")) == (True, 0, 0.0)
    assert len(session.urls) == 1


def test_poll_without_any_answer_is_unjudged(monkeypatch):
    """همه‌ی نودها تا مهلت null ماندند: این «بسته» نیست، «بی‌حکم» است.

    تفاوتش در دسته حیاتی است: بی‌حکم کانفیگ را حذف نمی‌کند.
    """
    _fast_poll(monkeypatch, interval=0.02, timeout=0.1)
    session = _FakeSession({node: None for node in IR3})
    assert _run(ch._poll(session, "rid")) == (False, 0, 0.0)


# ─── یک endpoint ──────────────────────────────────────────


def test_check_endpoint_reports_submit_error(monkeypatch):
    async def boom(session, endpoint):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(ch, "_submit", boom)
    nodes, ms, reason = _run(ch.check_endpoint(None, "1.2.3.4:443"))
    assert (nodes, ms) == (0, 0.0)
    assert reason.startswith("api:")


def test_check_endpoint_without_request_id(monkeypatch):
    """بدون request_id هم باید خطای API حساب شود (پیشوند api)."""
    async def none(session, endpoint):
        return None

    monkeypatch.setattr(ch, "_submit", none)
    assert _run(ch.check_endpoint(None, "1.2.3.4:443"))[2].startswith("api:")


def test_check_endpoint_rate_limited_penalizes_gate(monkeypatch):
    """۴۲۹ هم «بی‌حکم» است و هم باید دروازه را کند کند."""
    async def limited(session, endpoint):
        raise ch._RateLimited("HTTP 429")

    monkeypatch.setattr(ch, "_submit", limited)
    gate = ch._Gate(0.0, 10.0)
    nodes, ms, reason = _run(ch.check_endpoint(None, "1.2.3.4:443", gate))
    assert (nodes, ms) == (0, 0.0)
    assert reason.startswith("api:") and "429" in reason
    assert gate.rate_limited == 1
    assert gate.gap >= 0.5      # فاصله بزرگ شد


def test_check_endpoint_blocked_from_iran(monkeypatch):
    async def submit(session, endpoint):
        return "rid"

    async def poll(session, request_id):
        return True, 0, 0.0

    monkeypatch.setattr(ch, "_submit", submit)
    monkeypatch.setattr(ch, "_poll", poll)
    assert _run(ch.check_endpoint(None, "1.2.3.4:443")) == (0, 0.0, "از ایران بسته است")


def test_check_endpoint_unjudged_is_not_blocked(monkeypatch):
    """نتیجه‌ی نیامده نباید «از ایران بسته است» گزارش شود."""
    async def submit(session, endpoint):
        return "rid"

    async def poll(session, request_id):
        return False, 0, 0.0

    monkeypatch.setattr(ch, "_submit", submit)
    monkeypatch.setattr(ch, "_poll", poll)
    assert _run(ch.check_endpoint(None, "1.2.3.4:443"))[2].startswith("api:")


def test_check_endpoint_alive(monkeypatch):
    async def submit(session, endpoint):
        return "rid"

    async def poll(session, request_id):
        return True, 2, 187.0

    monkeypatch.setattr(ch, "_submit", submit)
    monkeypatch.setattr(ch, "_poll", poll)
    assert _run(ch.check_endpoint(None, "1.2.3.4:443")) == (2, 187.0, "")


# ─── دسته ─────────────────────────────────────────────────

def _stub_check(monkeypatch, table, counter=None):
    """table: endpoint → (nodes_ok, ms, reason)."""
    async def fake(session, endpoint, gate=None):
        if counter is not None:
            counter.append(endpoint)
        return table.get(endpoint, (0, 0.0, "از ایران بسته است"))

    monkeypatch.setattr(ch, "check_endpoint", fake)
    monkeypatch.setattr(ch, "SKIP_CHECKHOST", False)
    monkeypatch.setattr(ch, "CHECKHOST_MIN_NODES", 1)
    monkeypatch.setattr(ch, "CHECKHOST_TARGET_ALIVE", 0)
    monkeypatch.setattr(ch, "CHECKHOST_MAX_ENDPOINTS", 0)
    monkeypatch.setattr(ch, "CHECKHOST_CONCURRENCY", 4)
    monkeypatch.setattr(ch, "CHECKHOST_MIN_GAP_SEC", 0.0)
    monkeypatch.setattr(ch, "CHECKHOST_MAX_GAP_SEC", 0.0)
    monkeypatch.setattr(ch, "CHECKHOST_MAX_429", 0)


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
    """host:port برای IPv6 مبهم است؛ به check-host فرستاده نمی‌شود.

    ولی حذف هم نمی‌شود: بی‌حکم است، پس بدون برچسب IR عبور می‌کند.
    """
    v6, v4 = cfg("[2001:db8::1]", 8443), cfg("1.1.1.1")
    seen = []
    _stub_check(monkeypatch, {"1.1.1.1:443": (1, 30.0, "")}, seen)
    valid, stats = _run(ch.check_iran_batch([v6, v4]))
    assert valid == [(v4, 30.0), (v6, 0.0)]     # تأییدشده اول
    assert seen == ["1.1.1.1:443"]
    assert stats["endpoints_total"] == 1
    assert (stats["verified"], stats["unverified"]) == (1, 1)


def test_batch_sorts_by_iran_latency(monkeypatch):
    slow, fast = cfg("1.1.1.1"), cfg("2.2.2.2")
    _stub_check(monkeypatch, {"1.1.1.1:443": (1, 300.0, ""),
                              "2.2.2.2:443": (1, 120.0, "")})
    valid, _ = _run(ch.check_iran_batch([slow, fast]))
    assert [c for c, _ in valid] == [fast, slow]


# ─── صرفه‌جویی در سهمیه ───────────────────────────────────

def test_batch_stops_at_target_alive(monkeypatch):
    """به محض رسیدن به هدف، endpoint بعدی تست نمی‌شود.

    تست‌نشده‌ها بی‌حکم‌اند: در خروجی می‌مانند ولی بعد از تأییدشده‌ها و بدون
    برچسب IR.
    """
    hosts = [f"{i}.{i}.{i}.{i}" for i in (1, 2, 3)]
    seen = []
    _stub_check(monkeypatch, {f"{h}:443": (1, 50.0, "") for h in hosts}, seen)
    monkeypatch.setattr(ch, "CHECKHOST_TARGET_ALIVE", 1)
    monkeypatch.setattr(ch, "CHECKHOST_CONCURRENCY", 1)

    valid, stats = _run(ch.check_iran_batch([cfg(h) for h in hosts]))
    assert len(seen) == 1
    assert stats["endpoints_checked"] == 1
    assert (stats["verified"], stats["unverified"]) == (1, 2)
    assert [ms for _, ms in valid] == [50.0, 0.0, 0.0]
    assert stats["endpoints_unknown"] == 2


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
    assert valid == [(cf, 40.0), (vps, 0.0)]   # CDN تأییدشده، VPS بی‌حکم


# ─── سهمیه‌ی API: بی‌حکم ≠ بسته ───────────────────────────
# اجرای واقعی ۲۰:۱۱ UTC: از ۶۰۰ endpoint، ۵۸۱ تا «HTTP 429» گرفتند و ۲۲۴۳
# کانفیگ به ۶ رسید. دفاع اصلی این است که خطای API کانفیگ را حذف نکند؛
# fail-open فقط تورِ آخر است.

def test_batch_api_error_does_not_drop_configs(monkeypatch):
    """۴۲۹ حکم نیست: کانفیگ می‌ماند، ولی تأییدشده‌ی ایران حساب نمی‌شود."""
    ok, limited = cfg("1.1.1.1"), cfg("2.2.2.2")
    _stub_check(monkeypatch, {"1.1.1.1:443": (1, 70.0, ""),
                              "2.2.2.2:443": (0, 0.0, "api: HTTP 429")})
    valid, stats = _run(ch.check_iran_batch([ok, limited]))
    assert valid == [(ok, 70.0), (limited, 0.0)]
    assert (stats["endpoints_alive"], stats["endpoints_blocked"]) == (1, 0)
    assert stats["endpoints_unknown"] == 1
    assert stats["api_errors"] == 1
    assert "fail_open" not in stats     # یک حکم گرفتیم، پس لایه کار کرد


def test_batch_stops_after_too_many_rate_limits(monkeypatch):
    """وقتی سهمیه تمام است، ادامه دادن فقط 429 بیشتر می‌گیرد."""
    hosts = [f"{i}.{i}.{i}.{i}" for i in range(1, 6)]
    seen = []

    async def limited(session, endpoint, gate=None):
        seen.append(endpoint)
        if gate is not None:
            await gate.penalize()
        return 0, 0.0, "api: HTTP 429"

    _stub_check(monkeypatch, {}, None)
    monkeypatch.setattr(ch, "check_endpoint", limited)
    monkeypatch.setattr(ch, "CHECKHOST_MAX_429", 2)
    monkeypatch.setattr(ch, "CHECKHOST_CONCURRENCY", 1)

    valid, stats = _run(ch.check_iran_batch([cfg(h) for h in hosts]))
    assert len(seen) == 2                       # بعد از دو 429 متوقف شد
    assert stats["stopped_rate_limited"] is True
    assert len(valid) == len(hosts)             # هیچ‌کدام حذف نشدند
    assert stats["unverified"] == len(hosts)


def test_batch_survives_a_mostly_rate_limited_run(monkeypatch):
    """بازسازی اجرای ۲۰:۱۱ — ۹۷٪ درخواست‌ها 429، ۳٪ حکم واقعی.

    آن اجرا ۲۲۴۳ کانفیگ را به ۶ رساند و خروجی نهایی صفر شد. حالا فقط برچسب
    تأیید ایران کم می‌شود، نه خودِ کانفیگ‌ها.
    """
    hosts = [f"10.0.{i // 254}.{i % 254}" for i in range(100)]
    configs = [cfg(h) for h in hosts]
    judged = {f"{hosts[0]}:443": (2, 60.0, ""), f"{hosts[1]}:443": (2, 90.0, "")}

    async def fake(session, endpoint, gate=None):
        return judged.get(endpoint, (0, 0.0, "api: HTTP 429"))

    _stub_check(monkeypatch, {})
    monkeypatch.setattr(ch, "check_endpoint", fake)
    monkeypatch.setattr(ch, "CHECKHOST_MIN_NODES", 2)

    valid, stats = _run(ch.check_iran_batch(configs))
    assert len(valid) == len(configs)          # سهمیه هیچ کانفیگی را حذف نکرد
    assert stats["verified"] == 2
    assert [ms for _, ms in valid[:2]] == [60.0, 90.0]      # تأییدشده‌ها اول
    assert stats["endpoints_blocked"] == 0
    assert stats["api_errors"] == 98


# ─── fail-open ────────────────────────────────────────────
# اگر خودِ check-host از کار بیفتد، «هیچ‌کدام زنده نیست» نتیجه‌گیری غلطی است؛
# در آن حالت این لایه کنار گذاشته می‌شود تا خروجی صفر نشود.

def test_batch_fails_open_when_api_errors_dominate(monkeypatch):
    configs = [cfg("1.1.1.1"), cfg("2.2.2.2")]
    _stub_check(monkeypatch, {ep: (0, 0.0, "api: HTTP 429")
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
    assert stats["endpoints_blocked"] == 2


def test_batch_empty_input(monkeypatch):
    _stub_check(monkeypatch, {})
    valid, stats = _run(ch.check_iran_batch([]))
    assert valid == []
    assert (stats["total"], stats["endpoints_total"]) == (0, 0)



