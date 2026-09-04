"""تست نقش ادمین و کنترل‌های ادمین در ربات.

دو قرارداد کاربر این‌جا تست می‌شوند:
  ۱. کاربر عادی و ادمین یکی نیستند — نه در کیبورد، نه در منوی دستورها، و
     مهم‌تر از همه نه در دسترسی.
  ۲. خاموش/مکث باید *ماندگار* باشد: نسخه‌ی قبلی فقط یک متغیر در حافظه بود و
     با هر deploy ربات خودش روشن می‌شد بدون اینکه ادمین بداند.
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot

ADMIN = 42
USER = 777


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
    def __init__(self, text: str = "", uid: int = USER):
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
    """وضعیت را به tmp_path می‌برد تا configs/ واقعی دست نخورد."""
    monkeypatch.setattr(bot, "BOT_STATE_FILE", str(tmp_path / "bot_state.json"))
    monkeypatch.setattr(bot, "ADMIN_IDS", {ADMIN})
    monkeypatch.setattr(bot, "BOT_ENABLED", True)
    monkeypatch.setattr(bot, "PUBLISH_PAUSED", False)
    monkeypatch.setattr(bot, "_publish_log", [])

    async def no_refresh(force: bool = False) -> None:
        return None

    monkeypatch.setattr(bot, "refresh_cache", no_refresh)
    return tmp_path / "bot_state.json"


def run(coro):
    return asyncio.run(coro)


# ─── وضعیت ماندگار ────────────────────────────────────────

def test_state_survives_restart(isolated, monkeypatch):
    """همان چیزی که با deploy می‌شکست: /off باید بعد از restart بماند."""
    monkeypatch.setattr(bot, "BOT_ENABLED", False)
    monkeypatch.setattr(bot, "PUBLISH_PAUSED", True)
    assert bot.save_bot_state() is True

    # «restart»: مقدارها به پیش‌فرض برمی‌گردند و از دیسک خوانده می‌شوند.
    monkeypatch.setattr(bot, "BOT_ENABLED", True)
    monkeypatch.setattr(bot, "PUBLISH_PAUSED", False)
    bot.load_bot_state()
    assert bot.BOT_ENABLED is False
    assert bot.PUBLISH_PAUSED is True


def test_missing_state_file_means_bot_is_on(isolated):
    """فایل نبود = اجرای اول. ربات نباید خاموش بالا بیاید."""
    assert not os.path.exists(isolated)
    bot.load_bot_state()
    assert bot.BOT_ENABLED is True
    assert bot.PUBLISH_PAUSED is False


@pytest.mark.parametrize("junk", ["{not json", "[]", '"text"', ""])
def test_broken_state_file_keeps_defaults(isolated, junk):
    isolated.parent.mkdir(parents=True, exist_ok=True)
    isolated.write_text(junk, encoding="utf-8")
    bot.load_bot_state()
    assert bot.BOT_ENABLED is True


def test_state_file_is_json_with_lf(isolated):
    bot.save_bot_state()
    raw = isolated.read_bytes()
    assert b"\r\n" not in raw
    assert set(json.loads(raw.decode("utf-8"))) == {"enabled", "publish_paused"}


def test_unsaveable_state_still_changes_the_flag(isolated, monkeypatch):
    """اگر دیسک read-only بود، /off باید *همین حالا* کار کند و ادمین خبردار
    شود که ماندگار نیست — نه اینکه دستور بی‌اثر بماند."""
    monkeypatch.setattr(bot, "BOT_STATE_FILE", str(isolated / "nope" / "x.json"))
    monkeypatch.setattr(bot.os, "replace", _boom)
    update = FakeUpdate(uid=ADMIN)
    run(bot.cmd_off(update, FakeContext()))
    assert bot.BOT_ENABLED is False
    assert "ذخیره نشد" in update.sent


def _boom(*args, **kwargs):
    raise OSError("read-only")


# ─── on / off / pause / resume ────────────────────────────

def test_off_blocks_users_but_never_the_admin(isolated):
    """اگر /off ادمین را هم قفل کند، ربات دیگر قابل بازیابی نیست."""
    run(bot.cmd_off(FakeUpdate(uid=ADMIN), FakeContext()))
    assert bot.BOT_ENABLED is False

    normal = FakeUpdate(uid=USER)
    run(bot.cmd_stats(normal, FakeContext()))
    assert "خاموش" in normal.sent

    back_on = FakeUpdate(uid=ADMIN)
    run(bot.cmd_on(back_on, FakeContext()))
    assert bot.BOT_ENABLED is True
    assert "روشن" in back_on.sent


def test_pause_touches_only_the_publish_loop(isolated):
    run(bot.cmd_pause(FakeUpdate(uid=ADMIN), FakeContext()))
    assert bot.PUBLISH_PAUSED is True
    assert bot.BOT_ENABLED is True          # کاربران بی‌تأثیرند

    resumed = FakeUpdate(uid=ADMIN)
    run(bot.cmd_resume(resumed, FakeContext()))
    assert bot.PUBLISH_PAUSED is False
    assert "ادامه" in resumed.sent


def test_toggle_button_flips_both_directions(isolated):
    run(bot.cmd_toggle_publish(FakeUpdate(uid=ADMIN), FakeContext()))
    assert bot.PUBLISH_PAUSED is True
    run(bot.cmd_toggle_publish(FakeUpdate(uid=ADMIN), FakeContext()))
    assert bot.PUBLISH_PAUSED is False


def test_manual_publish_works_while_paused(isolated, monkeypatch):
    """قرارداد: مکث فقط خودکار را می‌گیرد؛ /publish دستی باید کار کند."""
    calls: list = []

    async def fake_once(bot_obj, trigger="auto"):
        calls.append(trigger)
        return {"selected": 1, "sent": 2, "failed": 0, "ids": []}

    monkeypatch.setattr(bot, "_publish_once", fake_once)
    monkeypatch.setattr(bot, "PUBLISH_PAUSED", True)
    run(bot.cmd_publish(FakeUpdate(uid=ADMIN), FakeContext()))
    assert calls == ["دستی"]


# ─── دروازه‌ی حلقه‌ی خودکار ─────────────────────────────────

class _Stop(Exception):
    """از دومین sleep پرتاب می‌شود تا حلقه پس از یک تیک تمام شود."""


def drive_loop(monkeypatch) -> list:
    """یک تیک از auto_publish_loop را اجرا می‌کند و برمی‌گردد.

    حلقه عمداً بی‌پایان است؛ sleep دوم (فاصله‌ی بین دوره‌ها) بیرون از try
    است، پس استثنا از آن‌جا حلقه را تمیز خاتمه می‌دهد.
    """
    calls: list = []

    async def fake_once(bot_obj, trigger="auto"):
        calls.append(trigger)
        return {}

    seen: list = []

    async def fake_sleep(seconds):
        seen.append(seconds)
        if len(seen) >= 2:
            raise _Stop

    monkeypatch.setattr(bot, "_publish_once", fake_once)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    app = type("App", (), {"bot": object(), "bot_data": {}})()
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(_Stop):
            loop.run_until_complete(bot.auto_publish_loop(app))
    finally:
        loop.close()
    return calls


def test_loop_publishes_when_running(isolated, monkeypatch):
    assert drive_loop(monkeypatch) == ["auto"]


def test_paused_loop_skips_the_tick_and_stays_alive(isolated, monkeypatch):
    """حلقه کشته نمی‌شود — /resume باید بدون restart کار کند."""
    monkeypatch.setattr(bot, "PUBLISH_PAUSED", True)
    assert drive_loop(monkeypatch) == []


def test_loop_skips_the_tick_while_bot_is_off(isolated, monkeypatch):
    monkeypatch.setattr(bot, "BOT_ENABLED", False)
    assert drive_loop(monkeypatch) == []


# ─── تفکیک نقش ────────────────────────────────────────────

def test_only_admin_gets_the_admin_rows(isolated):
    assert bot.keyboard_for(USER) is bot.MAIN_KEYBOARD
    assert bot.keyboard_for(None) is bot.MAIN_KEYBOARD
    assert bot.keyboard_for(ADMIN) is bot.ADMIN_KEYBOARD

    admin_texts = {
        b.text for row in bot.ADMIN_KEYBOARD.keyboard for b in row
    }
    user_texts = {b.text for row in bot.MAIN_KEYBOARD.keyboard for b in row}
    assert bot.BTN_A_PUBLISH in admin_texts
    assert bot.BTN_A_PUBLISH not in user_texts
    # خواسته‌ی کاربر: «دکمه‌های مخصوص کاربر نباید برای ادمین دیده شوند.» پس
    # کیبورد ادمین جمعِ دو تا نیست، دو مجموعه‌ی جدا هستند. (قبلاً این تست
    # می‌گفت ردیف‌های ادمین *بیشتر* است — همان جمع بودن که کاربر نمی‌خواست.)
    assert not (admin_texts & user_texts)
    assert bot.BTN_BEST in user_texts and bot.BTN_BEST not in admin_texts


def test_admin_button_text_from_a_normal_user_is_refused(isolated, monkeypatch):
    """کیبورد لایه‌ی راحتی است، نه امنیت: متن دکمه قابل تایپ‌کردن است."""
    calls: list = []

    async def fake_once(bot_obj, trigger="auto"):
        calls.append(trigger)
        return {}

    monkeypatch.setattr(bot, "_publish_once", fake_once)
    update = FakeUpdate(bot.BTN_A_PUBLISH, uid=USER)
    run(bot.handle_button(update, FakeContext()))
    assert calls == []
    assert "فقط ادمین" in update.sent


def test_admin_button_works_for_the_admin(isolated, monkeypatch):
    calls: list = []

    async def fake_once(bot_obj, trigger="auto"):
        calls.append(trigger)
        return {"selected": 10, "sent": 11, "failed": 0, "ids": []}

    monkeypatch.setattr(bot, "_publish_once", fake_once)
    run(bot.handle_button(FakeUpdate(bot.BTN_A_PUBLISH, uid=ADMIN), FakeContext()))
    assert calls == ["دستی"]


def test_every_admin_command_is_gated(isolated):
    """هیچ دستور ادمینی بدون @admin_only جا نماند."""
    for name, _desc, handler in bot.ADMIN_COMMANDS:
        update = FakeUpdate(uid=USER)
        run(handler(update, FakeContext()))
        assert "فقط ادمین" in update.sent, f"/{name} گیت ندارد"


def test_whoami_is_open_and_shows_only_your_own_id(isolated):
    """کاربر عادی باید شناسه‌ی خودش را ببیند (برای درخواست دسترسی) و
    هیچ شناسه‌ی دیگری نبیند."""
    update = FakeUpdate(uid=USER)
    run(bot.cmd_whoami(update, FakeContext()))
    assert str(USER) in update.sent
    assert str(ADMIN) not in update.sent
    assert "کاربر عادی" in update.sent

    admin = FakeUpdate(uid=ADMIN)
    run(bot.cmd_whoami(admin, FakeContext()))
    assert "ادمین" in admin.sent


# ─── منوی دستورها ─────────────────────────────────────────

class FakeBot:
    def __init__(self):
        self.menus: list = []

    async def set_my_commands(self, commands, scope=None):
        self.menus.append(([c.command for c in commands], scope))


def test_admin_menu_is_registered_only_for_admin_chats(isolated):
    app = type("App", (), {"bot": FakeBot()})()
    run(bot.setup_commands(app))

    public, admin_scoped = app.bot.menus[0], app.bot.menus[1:]
    assert public[1] is None
    assert "publish" not in public[0]        # کاربر عادی نمی‌بیندش
    assert "get" in public[0]

    assert [scope.chat_id for _cmds, scope in admin_scoped] == [ADMIN]
    assert "publish" in admin_scoped[0][0]
    assert "get" in admin_scoped[0][0]       # ادمین کاربر هم هست


def test_a_failing_admin_menu_does_not_break_startup(isolated, monkeypatch):
    """ادمینی که هیچ‌وقت به ربات پیام نداده chat ندارد → خطای تلگرام."""
    class AngryBot(FakeBot):
        async def set_my_commands(self, commands, scope=None):
            if scope is not None:
                raise RuntimeError("chat not found")
            await FakeBot.set_my_commands(self, commands, scope)

    monkeypatch.setattr(bot, "ADMIN_IDS", {ADMIN, 43})
    app = type("App", (), {"bot": AngryBot()})()
    run(bot.setup_commands(app))             # نباید پرتاب کند
    assert len(app.bot.menus) == 1


# ─── ابزارهای ادمین ───────────────────────────────────────

def test_donations_report_shows_the_queue(isolated, tmp_path, monkeypatch):
    from src import donations

    monkeypatch.setenv("DONATE_SALT", "test-salt-not-a-secret")
    monkeypatch.setattr(
        donations, "DONATIONS_FILE", str(tmp_path / "donations.json")
    )
    uuid = "3f8b1c2a-9d4e-4a7b-8c1d-2e3f4a5b6c7d"
    donations.add(
        [f"vless://{uuid}@edge.example.com:443?security=tls&sni=edge.example.com"],
        user_id=987654321,
    )
    update = FakeUpdate(uid=ADMIN)
    run(bot.cmd_donations(update, FakeContext()))
    assert "صف اهدا" in update.sent
    assert "987654321" not in update.sent    # حریم خصوصی اهداکننده


def test_donations_requeue_is_admin_only_and_manual(isolated, tmp_path, monkeypatch):
    from src import donations

    monkeypatch.setenv("DONATE_SALT", "test-salt-not-a-secret")
    monkeypatch.setattr(
        donations, "DONATIONS_FILE", str(tmp_path / "donations.json")
    )
    uuid = "3f8b1c2a-9d4e-4a7b-8c1d-2e3f4a5b6c7d"
    donations.add(
        [f"vless://{uuid}@edge2.example.com:443?security=tls&sni=edge2.example.com"],
        user_id=7,
    )
    donations.take_for_cycle(1)
    assert donations.stats()["taken"] == 1

    run(bot.cmd_donations(FakeUpdate(uid=ADMIN), FakeContext(["requeue"])))
    assert donations.stats()["taken"] == 0
    assert donations.stats()["queued"] == 1


def test_cycle_report_is_empty_before_any_cycle(isolated):
    update = FakeUpdate(uid=ADMIN)
    run(bot.cmd_cycle(update, FakeContext()))
    assert "دوره‌های انتشار" in update.sent


def test_cycle_report_shows_quota_shortfall(isolated, monkeypatch):
    """دقیقاً همان چیزی که مشکل «۳ کانفیگ از ۱۰» را قابل دیدن می‌کند."""
    monkeypatch.setattr(bot, "_publish_log", [{
        "at": "10:00 UTC", "trigger": "auto", "selected": 3, "sent": 4,
        "failed": 0, "pool_size": 3, "reserve_size": 0, "from_pool": 0,
        "donated": 0, "quota_short": 7,
    }])
    update = FakeUpdate(uid=ADMIN)
    run(bot.cmd_cycle(update, FakeContext()))
    assert "کمبود سهمیه: 7" in update.sent


def test_health_report_handles_a_missing_file(isolated, monkeypatch):
    monkeypatch.setattr(bot, "RAW_BASE", "")
    monkeypatch.setattr(bot, "HEALTH_FILE", str(isolated.parent / "none.json"))
    update = FakeUpdate(uid=ADMIN)
    run(bot.cmd_health(update, FakeContext()))
    assert "پیدا نشد" in update.sent


def test_health_report_renders_dead_sources(isolated, tmp_path, monkeypatch):
    path = tmp_path / "health.json"
    path.write_text(json.dumps({
        "updated_at": "2026-09-03T10:00:00Z",
        "by_kind": {"github": {"sources": 3, "ok": 2, "dead": 1, "configs": 90}},
        "dead_sources": [{"kind": "web", "name": "sub.example.com", "error": "404"}],
        "top_sources": [{"kind": "github", "name": "repo/x", "count": 60}],
    }), encoding="utf-8")
    monkeypatch.setattr(bot, "RAW_BASE", "")
    monkeypatch.setattr(bot, "HEALTH_FILE", str(path))
    update = FakeUpdate(uid=ADMIN)
    run(bot.cmd_health(update, FakeContext()))
    assert "sub.example.com" in update.sent
    assert "404" in update.sent
    assert "repo/x" in update.sent

