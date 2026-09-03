"""تست لایه ۷: تأخیر واقعی + لرزش + افت بسته + سرعت.

خواسته‌ی صریح کاربر: «نودی با پینگ ۱۰۰ms و ۰٪ افت از نودی با پینگ ۵۰ms و
۲۰٪ افت بسیار ارزشمندتر است» — پس این سه سنجه باید *جدا* از تأخیر محاسبه،
جمع‌بندی و در برچسب کانفیگ ثبت شوند. این‌جا ریاضیاتِ آن سنجه‌ها، مسیر نجات
گیت سرعت، و سازگاری قرارداد خروجی آزمایش می‌شود (بدون شبکه و بدون xray).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import vless
from src.tester import http_tester
from src.tester.http_tester import Probe

UUID = "11111111-2222-3333-4444-555555555555"


def cfg(name: str) -> str:
    return f"vless://{UUID}@{name}.example.com:443?security=tls&type=tcp#{name}"


class FakeProbe:
    """جای probe_config — برای هر کانفیگ یک صف از نتیجه‌های آماده."""

    def __init__(self, plan: dict):
        self.plan = {key: list(value) for key, value in plan.items()}
        self.calls = 0

    async def __call__(self, config: str) -> Probe:
        self.calls += 1
        queue = self.plan.get(config) or []
        return queue.pop(0) if queue else Probe(reason="صف تمام شد")


def ok_probe(delay=100.0, loss=0.0, jitter=5.0, speed=400.0) -> Probe:
    return Probe(
        ok=True, delay_ms=delay, loss_pct=loss, jitter_ms=jitter, speed_kbps=speed
    )


def slow_probe(delay=100.0, loss=0.0, jitter=5.0) -> Probe:
    """کانفیگی که همه‌ی probe ها را پاس کرد و فقط سرعتش کم بود."""
    return Probe(
        ok=False, delay_ms=delay, loss_pct=loss, jitter_ms=jitter,
        speed_kbps=12.0, reason="سرعت 12.0 KB/s", speed_only_fail=True,
    )


def _patch(monkeypatch, fake, rescue_min=3, rounds=1):
    monkeypatch.setattr(http_tester, "probe_config", fake)
    monkeypatch.setattr(http_tester, "SPEED_RESCUE_MIN", rescue_min)
    monkeypatch.setattr(http_tester, "HTTP_TEST_ROUNDS", rounds)
    monkeypatch.setattr(http_tester, "HTTP_ROUND_GAP_SEC", 0)


# ─── لرزش ─────────────────────────────────────────────────

def test_jitter_is_mean_of_successive_differences():
    # |120-100| = 20 و |110-120| = 10 → میانگین ۱۵
    assert http_tester._jitter([100.0, 120.0, 110.0]) == 15.0


def test_jitter_needs_two_samples():
    """یک probe لرزش ندارد — ‏-1 یعنی «اندازه‌گیری نشد»، نه «صفر»."""
    assert http_tester._jitter([100.0]) == -1.0
    assert http_tester._jitter([]) == -1.0


def test_jitter_is_sensitive_to_order():
    """چرا اختلاف پیاپی و نه انحراف معیار: ترتیب برای کاربر مهم است."""
    swingy = http_tester._jitter([50.0, 150.0, 50.0, 150.0])
    steady = http_tester._jitter([50.0, 50.0, 150.0, 150.0])
    assert swingy > steady


def test_stable_tunnel_has_zero_jitter():
    assert http_tester._jitter([80.0, 80.0, 80.0]) == 0.0


# ─── جمع‌بندی چند دور ─────────────────────────────────────

def test_aggregate_averages_loss_and_medians_speed():
    probes = [
        ok_probe(loss=0.0, jitter=10.0, speed=100.0),
        ok_probe(loss=25.0, jitter=20.0, speed=900.0),
        ok_probe(loss=50.0, jitter=30.0, speed=300.0),
    ]
    agg = http_tester._aggregate(probes)
    assert agg["loss_pct"] == 25.0      # میانگین ۰ و ۲۵ و ۵۰
    assert agg["jitter_ms"] == 20.0     # میانگین ۱۰ و ۲۰ و ۳۰
    assert agg["speed_kbps"] == 300.0   # میانه، پس ۹۰۰ عدد را باد نمی‌کند


def test_aggregate_keeps_unmeasured_negative():
    """اگر هیچ دوری سنجه را نداد، «نامعلوم» می‌ماند و ۰ جا نمی‌زند."""
    agg = http_tester._aggregate([Probe(ok=True, delay_ms=90.0)])
    assert agg["loss_pct"] == -1.0
    assert agg["jitter_ms"] == -1.0
    assert agg["speed_kbps"] == 0.0


def test_aggregate_ignores_rescued_zero_speed():
    """سرعتِ نجات‌داده‌شده (۰) میانه را به سمت صفر نمی‌کشد."""
    agg = http_tester._aggregate([ok_probe(speed=0.0), ok_probe(speed=500.0)])
    assert agg["speed_kbps"] == 500.0


# ─── دروازه‌ی سرعت و مسیر نجات ────────────────────────────

def test_speed_rejects_are_rescued_when_nothing_passed(monkeypatch):
    """گیت سرعت همه را رد کرد → تقصیر بنچمارک است، نه کانفیگ‌ها.

    قاعده‌ی پروژه: «تست نشد ≠ رد شد». کانفیگ عبور می‌کند ولی سرعتش
    «اندازه‌گیری‌نشده» ثبت می‌شود تا برچسب عدد دروغ ندهد.
    """
    configs = [cfg(f"s{i}") for i in range(3)]
    fake = FakeProbe({c: [slow_probe()] for c in configs})
    _patch(monkeypatch, fake, rescue_min=3)
    passed, failed, reasons, quality = asyncio.run(http_tester._one_round(configs))
    assert [c for c, _ in passed] == configs
    assert (failed, reasons) == (0, {})
    assert all(quality[c].speed_kbps == 0.0 for c in configs)
    assert all(quality[c].loss_pct == 0.0 for c in configs)


def test_speed_rejects_stay_rejected_when_someone_passed(monkeypatch):
    """یک کانفیگ سرعت خوب داد، پس بنچمارک سالم است و کندها واقعاً کندند."""
    fast, slow = cfg("fast"), cfg("slow")
    fake = FakeProbe({fast: [ok_probe()], slow: [slow_probe()]})
    _patch(monkeypatch, fake, rescue_min=1)
    passed, failed, reasons, quality = asyncio.run(
        http_tester._one_round([fast, slow])
    )
    assert [c for c, _ in passed] == [fast]
    assert failed == 1
    assert reasons == {"سرعت 12.0 KB/s": 1}
    assert slow not in quality


def test_rescue_needs_a_quorum(monkeypatch):
    """یک ردشدنِ تنها نجات نمی‌گیرد؛ نشانه‌ی خرابی بنچمارک «همه» است."""
    only = cfg("only")
    fake = FakeProbe({only: [slow_probe()]})
    _patch(monkeypatch, fake, rescue_min=3)
    passed, failed, _, _ = asyncio.run(http_tester._one_round([only]))
    assert (passed, failed) == ([], 1)


def test_real_failures_are_never_rescued(monkeypatch):
    """رد شدن به‌خاطر تأخیر یا افت بسته با نجاتِ سرعت قاطی نمی‌شود."""
    late, lossy = cfg("late"), cfg("lossy")
    fake = FakeProbe({
        late: [Probe(reason="تأخیر واقعی 4200ms")],
        lossy: [Probe(reason="افت بسته 75%")],
    })
    _patch(monkeypatch, fake, rescue_min=1)
    passed, failed, reasons, _ = asyncio.run(http_tester._one_round([late, lossy]))
    assert (passed, failed) == ([], 2)
    assert set(reasons) == {"تأخیر واقعی 4200ms", "افت بسته 75%"}


# ─── قرارداد خروجی batch ──────────────────────────────────

def test_batch_keeps_pair_contract_and_ships_quality(monkeypatch):
    """خروجی همان دوگانه است؛ سنجه‌ها از کانال کناری _quality می‌آیند."""
    good = cfg("good")
    fake = FakeProbe({good: [ok_probe(delay=120.0, loss=0.0, speed=430.0)]})
    _patch(monkeypatch, fake, rounds=1)
    valid, stats = asyncio.run(http_tester.http_test_batch([good]))
    assert valid == [(good, 120.0)]
    assert stats["_quality"][good] == {
        "loss_pct": 0.0, "jitter_ms": 5.0, "speed_kbps": 430.0
    }
    assert stats["avg_speed_kbps"] == 430.0
    assert stats["zero_loss"] == 1


def test_batch_requires_every_round(monkeypatch):
    """کانفیگی که دور دوم افتاد، حتی با دور اول موفق، بیرون می‌ماند."""
    flaky, solid = cfg("flaky"), cfg("solid")
    fake = FakeProbe({
        flaky: [ok_probe(), Probe(reason="همه URL ها fail شدن")],
        solid: [ok_probe(delay=90.0), ok_probe(delay=110.0)],
    })
    _patch(monkeypatch, fake, rounds=2)
    valid, stats = asyncio.run(http_tester.http_test_batch([flaky, solid]))
    assert [c for c, _ in valid] == [solid]
    assert valid[0][1] == 100.0            # میانه‌ی ۹۰ و ۱۱۰
    assert stats["passed"] == 1 and stats["failed"] == 1
    assert len(stats["round_stats"]) == 2


def test_batch_reports_unmeasured_speed_without_lying(monkeypatch):
    """وقتی سرعت اندازه‌گیری نشد، میانگین صفر است نه عددی ساختگی."""
    rescued = cfg("rescued")
    fake = FakeProbe({rescued: [ok_probe(speed=0.0, loss=25.0)]})
    _patch(monkeypatch, fake, rounds=1)
    _, stats = asyncio.run(http_tester.http_test_batch([rescued]))
    assert stats["avg_speed_kbps"] == 0
    assert stats["speed_measured"] == 0
    assert stats["avg_loss_pct"] == 25.0
    assert stats["zero_loss"] == 0


# ─── پیام خطای xray ───────────────────────────────────────

def test_clean_error_reports_the_root_cause():
    """پیام واقعی مهم است، نه دنباله‌ی جمله‌ی راهنما.

    نسخه‌ی قبلی ۸۰ کاراکتر آخر لاگ را برمی‌داشت، پس کاربر همیشه فقط
    «Please update your config(s)...» را می‌دید و کلیدِ مقصر پیدا نمی‌شد.
    """
    log = (
        'main: failed to load config files: [/tmp/tmpx.json] > '
        'infra/conf: unknown fingerprint "hellogolang". '
        'Please update your config(s) according to release note and documentation.'
    )
    assert http_tester.clean_error(log) == 'infra/conf: unknown fingerprint "hellogolang"'


def test_clean_error_survives_empty_and_plain_logs():
    assert http_tester.clean_error("") == ""
    assert http_tester.clean_error("   \n \n") == ""
    assert http_tester.clean_error("connection refused") == "connection refused"


# ─── برچسب: سه سنجه‌ی تازه ────────────────────────────────

def test_tag_round_trip_of_new_fields():
    tagged = vless.add_tag(
        cfg("n"), 84.0, "NL", 212.0, loss_pct=0.0, jitter_ms=9.0, speed_kbps=430.0
    )
    assert tagged.endswith("#n|NL|84ms|IR212|P0%|J9ms|S430KB")
    assert vless.get_loss_pct(tagged) == 0.0
    assert vless.get_jitter_ms(tagged) == 9.0
    assert vless.get_speed_kbps(tagged) == 430.0


def test_new_fields_do_not_confuse_old_scanners():
    """J9ms نباید به‌جای تأخیر خوانده شود و S430KB به‌جای کد کشور."""
    tagged = vless.add_tag(
        cfg("n"), 84.0, "NL", 212.0, loss_pct=0.0, jitter_ms=9.0, speed_kbps=430.0
    )
    assert vless.get_latency(tagged) == "84ms"
    assert vless.get_latency_ms(tagged) == 84.0
    assert vless.get_country(tagged) == "NL"
    assert vless.get_iran_ms(tagged) == 212.0
    assert vless.get_name(tagged) == "n"


def test_unmeasured_stability_is_not_tagged_as_perfect():
    """بی‌برچسب ماندن «نامعلوم» است؛ ۰٪ افت ادعای بزرگی است و باید سنجیده شود."""
    plain = vless.add_tag(cfg("n"), 84.0, "NL")
    assert "|P" not in plain and "|J" not in plain and "|S" not in plain
    assert vless.get_loss_pct(plain) == -1.0
    assert vless.get_jitter_ms(plain) == -1.0
    assert vless.get_speed_kbps(plain) == 0.0
    assert vless.is_stable(plain) is False
    assert vless.is_stable(vless.add_tag(cfg("n"), 84.0, "NL", loss_pct=0.0)) is True
