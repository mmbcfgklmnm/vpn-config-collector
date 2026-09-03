"""تست گزارش کیفیت ادمین (/quality) و خطوط لایه ۷ در /test.

قاعده‌ی مرکزی همان قاعده‌ی کارت مشخصات است، این بار در سمت ادمین: گزارش فقط
چیزی را می‌گوید که اندازه‌گیری شده. اگر پول ذخیره‌ی تست‌نشده در میانگین افت
بسته بیاید، میانگین مصنوعی صفر می‌شود و ادمین فکر می‌کند همه‌چیز سالم است —
دقیقاً همان ادعای دروغی که در کانال جلویش گرفته شد.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
from src import clean_ip, vless
from src.tester.http_tester import Probe

ADMIN = 42
USER = 777
UUID = "11111111-2222-3333-4444-555555555555"

CDN = (
    f"vless://{UUID}@104.16.5.9:443?type=ws&security=tls"
    "&host=cdn.example.com&path=%2Fws#Node"
)


class FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.sent: list = []

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.first_name = "T"


class FakeUpdate:
    def __init__(self, text: str = "", uid: int = ADMIN):
        self.effective_message = FakeMessage(text)
        self.effective_user = FakeUser(uid)

    @property
    def sent(self) -> str:
        return "\n".join(self.effective_message.sent)


class FakeContext:
    def __init__(self, args=None):
        self.args = args or []
        self.bot = object()
        self.user_data: dict = {}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "BOT_STATE_FILE", str(tmp_path / "bot_state.json"))
    monkeypatch.setattr(bot, "ADMIN_IDS", {ADMIN})
    monkeypatch.setattr(bot, "BOT_ENABLED", True)

    async def no_refresh(force: bool = False) -> None:
        return None

    monkeypatch.setattr(bot, "refresh_cache", no_refresh)
    monkeypatch.setattr(bot, "load_configs", lambda: [])
    monkeypatch.setattr(bot, "load_pool_configs", lambda: [])
    monkeypatch.setattr(bot, "load_stats", lambda: {})


def run(coro):
    return asyncio.run(coro)


def named(name: str, **kwargs) -> str:
    return vless.add_tag(CDN.replace("#Node", f"#{name}"), 118.0, "DE", 205.0, **kwargs)


# ─── تفکیک «اندازه‌گیری‌شده» از «تست‌نشده» ─────────────────

def test_an_empty_pool_says_so_instead_of_printing_zeros():
    assert bot._quality_report([]) == ["_پول خالی است._"]


def test_measured_zero_loss_is_reported_as_such():
    report = "\n".join(bot._quality_report([
        named("a", loss_pct=0.0, jitter_ms=9.0, speed_kbps=430.0),
        named("b", loss_pct=0.0, jitter_ms=11.0, speed_kbps=530.0),
    ]))
    assert "بدون افت بسته: 2/2" in report
    assert "480.0 KB/s" in report          # میانگین سرعت
    assert "بدون سنجه" not in report       # چیزی اندازه‌گیری‌نشده نمانده


def test_untested_configs_are_counted_apart_and_left_out_of_the_average():
    """۲ کانفیگ با ۲۰٪ افت + ۲ تست‌نشده → میانگین ۲۰٪ است، نه ۱۰٪."""
    report = "\n".join(bot._quality_report([
        named("a", loss_pct=20.0), named("b", loss_pct=20.0),
        named("c"), named("d"),
    ]))
    assert "میانگین افت 20.0%" in report
    assert "بدون افت بسته: 0/2" in report
    assert "بدون سنجه‌ی پایداری: 2" in report
    assert "تست نشد» ≠ «رد شد" in report


def test_a_fully_untested_pool_claims_no_stability_at_all():
    report = "\n".join(bot._quality_report([named("a"), named("b")]))
    assert "لرزش: اندازه‌گیری نشد" in report
    assert "سرعت: اندازه‌گیری نشد" in report
    assert "بدون سنجه‌ی پایداری: 2" in report


def test_revived_and_iran_verified_configs_are_counted():
    revived = vless.add_tag(clean_ip.revive(CDN, "172.67.1.2"), 90.0, "DE", 210.0)
    report = "\n".join(bot._quality_report([revived, named("plain")]))
    assert "احیاشده با IP تمیز کلودفلر: 1" in report
    assert "🇮🇷 2 تأییدشده از ایران" in report


def test_the_best_latency_is_shown_next_to_the_average():
    report = "\n".join(bot._quality_report([
        vless.add_tag(CDN, 100.0, "DE", 205.0),
        vless.add_tag(CDN.replace("#Node", "#x"), 300.0, "DE", 205.0),
    ]))
    assert "میانگین 200.0ms" in report
    assert "بهترین 100ms" in report


# ─── دستور ────────────────────────────────────────────────

def test_quality_is_admin_only():
    update = FakeUpdate(uid=USER)
    run(bot.cmd_quality(update, FakeContext()))
    assert "فقط ادمین" in update.sent


def test_quality_separates_the_verified_pool_from_the_reserve(monkeypatch):
    """پول ذخیره سنجه ندارد؛ اگر با تأییدشده قاطی شود آمار دروغ می‌شود."""
    monkeypatch.setattr(
        bot, "load_configs", lambda: [named("good", loss_pct=0.0, speed_kbps=400.0)]
    )
    monkeypatch.setattr(bot, "load_pool_configs", lambda: [named("reserve")])
    update = FakeUpdate(uid=ADMIN)
    run(bot.cmd_quality(update, FakeContext()))
    assert "کیفیت پول تأییدشده" in update.sent
    assert "پول ذخیره (تست‌نشده)* — 1 کانفیگ" in update.sent
    assert "بدون افت بسته: 1/1" in update.sent


def test_quality_reports_the_last_pipeline_summary(monkeypatch):
    monkeypatch.setattr(bot, "load_stats", lambda: {"pipeline": {"summary": {
        "funnel": [248, 240, 88, 44, 40, 40, 12, 10],
        "stable": 8, "avg_speed_kbps": 430.0, "revived": 2,
    }}})
    update = FakeUpdate(uid=ADMIN)
    run(bot.cmd_quality(update, FakeContext()))
    assert "248 → 240 → 88 → 44" in update.sent
    assert "430.0 KB/s" in update.sent


def test_quality_survives_a_stats_file_without_a_pipeline_key(monkeypatch):
    monkeypatch.setattr(bot, "load_stats", lambda: {"pipeline": None})
    update = FakeUpdate(uid=ADMIN)
    run(bot.cmd_quality(update, FakeContext()))
    assert "پول خالی است" in update.sent
    assert "آخرین اجرای pipeline" not in update.sent


# ─── خطوط تونل در /test ───────────────────────────────────

def test_a_missing_xray_is_grey_not_a_failure():
    """روی هاست ربات xray نیست؛ ❌ یعنی «کانفیگ رد شد» که دروغ است."""
    lines = bot._probe_lines(Probe(reason="xray پیدا نشد"))
    assert len(lines) == 1
    assert lines[0].startswith("⚪")
    assert "تست نشد ≠ رد شد" in lines[0]


def test_a_healthy_probe_reports_delay_stability_and_speed():
    text = "\n".join(bot._probe_lines(Probe(
        ok=True, delay_ms=118.0, jitter_ms=9.0, loss_pct=0.0, speed_kbps=430.0
    )))
    assert "✅ تونل واقعی: 118ms" in text
    assert "بدون افت بسته" in text and "لرزش 9ms" in text
    assert "⬇️ سرعت: 430 KB/s" in text


def test_a_failed_probe_names_the_gate_that_rejected_it():
    text = "\n".join(bot._probe_lines(Probe(
        ok=False, delay_ms=2500.0, loss_pct=40.0, jitter_ms=300.0,
        reason="افت بسته 40%",
    )))
    assert text.startswith("❌ تونل واقعی: 2500ms")
    assert "دلیل رد: افت بسته 40%" in text


def test_a_speed_only_failure_is_flagged_as_such():
    text = "\n".join(bot._probe_lines(Probe(
        ok=False, delay_ms=118.0, loss_pct=0.0, jitter_ms=5.0,
        speed_kbps=20.0, reason="سرعت کم", speed_only_fail=True,
    )))
    assert "فقط روی گیت سرعت افتاد" in text


def test_an_unmeasured_probe_makes_no_stability_claim():
    text = "\n".join(bot._probe_lines(Probe(reason="همه URL ها fail شدن")))
    assert "⚪ پایداری: اندازه‌گیری نشد" in text
    assert "⚪ سرعت: اندازه‌گیری نشد" in text
    assert "بدون افت بسته" not in text
