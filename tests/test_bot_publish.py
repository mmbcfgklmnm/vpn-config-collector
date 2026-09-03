"""تست سیم‌کشی دوره‌ی انتشار در ربات (bot._publish_once).

مشکل گزارش‌شده‌ی کاربر: «ربات فقط کانفیگ‌هایی می‌فرستد که پینگ ایران دارند و
سهمیه‌ی ۱۰تایی می‌شکند — در دوره‌های آخر فقط ۳ کانفیگ رفت.» انتخاب و ترتیب در
publisher تست شده؛ این‌جا تست می‌شود که ربات *هر سه منبع* را به آن می‌دهد:
پول تأییدشده (داخلی + بین‌المللی)، پول ذخیره‌ی تست‌نشده، و صف اهدا.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
from src import donations, vless

UUID = "11111111-2222-3333-4444-555555555555"


def cfg(n: int, latency: float = 100.0, iran: float = 0.0, country: str = "NL") -> str:
    raw = f"vless://{UUID}@10.0.0.{n}:443?security=tls&type=tcp#node{n}"
    return vless.add_tag(raw, latency, country, iran)


IRAN_CFG = cfg(1, 300.0, iran=180.0)
INTL_CFG = cfg(2, 40.0)
POOL_CFG = cfg(3, 60.0)
MANUAL_CFG = f"vless://{UUID}@10.0.0.4:443?security=tls&type=tcp#manual"
DONATED = f"vless://{UUID}@edge.example.com:443?security=tls&sni=edge.example.com"


class FakePublisher:
    """آخرین آرگومان‌های publish_batch را نگه می‌دارد."""

    calls: list = []
    fail_donated: bool = False

    def __init__(self, bot=None):
        self.bot = bot

    async def publish_batch(self, configs, stats=None, reserve=None, donated=None):
        FakePublisher.calls.append({
            "configs": list(configs), "reserve": list(reserve or []),
            "donated": list(donated or []),
        })
        sent = [] if FakePublisher.fail_donated else list(donated or [])
        return {
            "selected": len(configs), "sent": len(configs) + len(sent),
            "failed": 0, "ids": [vless.short_id(c) for c in configs],
            "from_pool": 0, "quota_short": 0, "donated_sent": sent,
        }


@pytest.fixture(autouse=True)
def wired(tmp_path, monkeypatch):
    """ربات را از شبکه و از فایل‌های واقعی مخزن جدا می‌کند."""
    FakePublisher.calls = []
    FakePublisher.fail_donated = False

    async def no_refresh(force: bool = False) -> None:
        return None

    monkeypatch.setattr(bot, "refresh_cache", no_refresh)
    monkeypatch.setattr(bot, "Publisher", FakePublisher)
    monkeypatch.setattr(bot, "load_stats", lambda: {})
    monkeypatch.setattr(bot, "_publish_log", [])
    for name in ("VALID_FILE", "IRAN_FILE", "INTL_FILE", "POOL_FILE",
                 "MANUAL_FILE", "STATS_FILE"):
        monkeypatch.setattr(bot, name, str(tmp_path / f"{name}.txt"))
    for key in ("configs", "iran", "pool"):
        monkeypatch.setitem(bot._cache, key, [])
    monkeypatch.setitem(bot._cache, "stats", {})
    # صف اهدا هم به tmp_path می‌رود تا فایل واقعی دست نخورد.
    monkeypatch.setenv("DONATE_SALT", "test-salt-not-a-secret")
    monkeypatch.setattr(donations, "DONATIONS_FILE", str(tmp_path / "donations.json"))
    return tmp_path


def run_once(trigger: str = "test") -> dict:
    return asyncio.run(bot._publish_once(object(), trigger))


def last_call() -> dict:
    assert FakePublisher.calls, "publish_batch صدا زده نشد"
    return FakePublisher.calls[-1]


# ─── پول انتشار ───────────────────────────────────────────

def test_publish_pool_merges_all_sources(wired, monkeypatch):
    monkeypatch.setitem(bot._cache, "configs", [INTL_CFG])
    monkeypatch.setitem(bot._cache, "iran", [IRAN_CFG])
    (wired / "INTL_FILE.txt").write_text(INTL_CFG + "\n", encoding="utf-8")
    (wired / "MANUAL_FILE.txt").write_text(MANUAL_CFG + "\n", encoding="utf-8")

    pool = bot.load_publish_pool()
    assert set(pool) == {INTL_CFG, IRAN_CFG, MANUAL_CFG}


def test_publish_pool_is_deduped_by_endpoint(wired, monkeypatch):
    """یک endpoint با دو برچسب تأخیر متفاوت سهمیه را دو بار نمی‌خورد."""
    monkeypatch.setitem(bot._cache, "configs", [INTL_CFG])
    monkeypatch.setitem(bot._cache, "iran", [vless.add_tag(INTL_CFG, 900.0, "DE", 0.0)])
    assert bot.load_publish_pool() == [INTL_CFG]


def test_international_configs_reach_publisher(wired, monkeypatch):
    """قلب مشکل کاربر: قبلاً فقط تأییدشده‌های ایران به انتشار می‌رسید."""
    monkeypatch.setitem(bot._cache, "configs", [INTL_CFG, IRAN_CFG])
    run_once()
    assert set(last_call()["configs"]) == {INTL_CFG, IRAN_CFG}


def test_pool_is_passed_as_reserve(wired, monkeypatch):
    monkeypatch.setitem(bot._cache, "configs", [IRAN_CFG])
    monkeypatch.setitem(bot._cache, "pool", [POOL_CFG])
    run_once()
    assert last_call()["reserve"] == [POOL_CFG]


def test_pool_read_from_disk_when_cache_empty(wired):
    (wired / "POOL_FILE.txt").write_text(POOL_CFG + "\n", encoding="utf-8")
    assert bot.load_pool_configs() == [POOL_CFG]


def test_reserve_not_passed_when_fill_disabled(wired, monkeypatch):
    monkeypatch.setattr(bot, "PUBLISH_FILL_FROM_POOL", False)
    monkeypatch.setitem(bot._cache, "configs", [IRAN_CFG])
    monkeypatch.setitem(bot._cache, "pool", [POOL_CFG])
    run_once()
    assert last_call()["reserve"] == []


def test_cycle_runs_on_reserve_alone(wired, monkeypatch):
    """اجرای بی‌خروجی همان جایی است که کانال به ذخیره نیاز دارد."""
    monkeypatch.setitem(bot._cache, "pool", [POOL_CFG])
    run_once()
    assert last_call() == {"configs": [], "reserve": [POOL_CFG], "donated": []}


def test_empty_everything_skips_the_cycle(wired):
    result = run_once()
    assert FakePublisher.calls == []
    assert result["selected"] == 0 and result["sent"] == 0


# ─── اهدایی‌ها ─────────────────────────────────────────────

def test_donated_configs_are_taken_and_marked_sent(wired, monkeypatch):
    monkeypatch.setitem(bot._cache, "configs", [IRAN_CFG])
    monkeypatch.setattr(bot, "PUBLISH_DONATED_COUNT", 1)
    assert donations.add([DONATED], user_id=42)["added"] == 1

    result = run_once()
    assert len(last_call()["donated"]) == 1
    assert result["donated"] == 1
    assert donations.stats()["sent"] == 1
    assert donations.stats()["queued"] == 0


def test_failed_donation_stays_taken_and_is_never_reposted(wired, monkeypatch):
    """قرارداد کاربر: هر اهدایی *حداکثر یک بار* پست می‌شود."""
    FakePublisher.fail_donated = True
    monkeypatch.setitem(bot._cache, "configs", [IRAN_CFG])
    monkeypatch.setattr(bot, "PUBLISH_DONATED_COUNT", 1)
    donations.add([DONATED], user_id=42)

    run_once()
    assert donations.stats()["sent"] == 0
    assert donations.stats()["taken"] == 1
    # دوره‌ی بعد دوباره برداشته نمی‌شود
    run_once()
    assert last_call()["donated"] == []


def test_donations_disabled_takes_nothing(wired, monkeypatch):
    monkeypatch.setattr(bot, "DONATE_ENABLED", False)
    monkeypatch.setitem(bot._cache, "configs", [IRAN_CFG])
    donations.add([DONATED], user_id=42)
    run_once()
    assert last_call()["donated"] == []
    assert donations.stats()["queued"] == 1


def test_cycle_runs_on_donations_alone(wired, monkeypatch):
    monkeypatch.setattr(bot, "PUBLISH_DONATED_COUNT", 2)
    donations.add([DONATED], user_id=42)
    run_once()
    assert last_call()["configs"] == []
    assert len(last_call()["donated"]) == 1


# ─── گزارش دوره ───────────────────────────────────────────

def test_result_reports_pool_sizes_for_admin_status(wired, monkeypatch):
    monkeypatch.setitem(bot._cache, "configs", [IRAN_CFG, INTL_CFG])
    monkeypatch.setitem(bot._cache, "pool", [POOL_CFG])
    result = run_once("دستی")
    assert result["pool_size"] == 2
    assert result["reserve_size"] == 1
    assert result["trigger"] == "دستی"
    assert bot._publish_log[-1]["pool_size"] == 2
