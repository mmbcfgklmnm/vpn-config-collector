"""تست کلید پنهان/نمایش کیبورد و رنگ دکمه‌ها.

خواسته‌ی کاربر دو تکه است: «دکمه‌ی خاموش/روشن کردن کیبورد» و «همه‌ی دکمه‌ها
رنگی و مرتب». تکه‌ی اول یک تله‌ی ظریف دارد که این فایل قفلش می‌کند: هر پاسخی
که `reply_markup=keyboard_for(...)` بفرستد، کیبوردِ تازه‌پنهان‌شده را همان
لحظه برمی‌گرداند و دکمه بی‌اثر به نظر می‌رسد. پس تست‌ها *خودِ* reply_markup
را می‌بینند، نه فقط متن پاسخ را.

تکه‌ی دوم روی محیط محلی قابل تست نیست (PTB 21.1.1 پارامتر style را نمی‌شناسد
و tg_ui آن را نمی‌فرستد تا کرش نکند)، پس این‌جا فقط چیزی تست می‌شود که در هر
دو نسخه درست است: متن هر دکمه به‌تنهایی گویا باشد و مسیر هر دکمه وجود داشته
باشد — رنگ تأکید است، تنها حامل معنا نیست.
"""
import asyncio
import os
import sys

import pytest
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot

ADMIN = 42
USER = 777


class FakeMessage:
    """پیام جعلی که kwargs را هم نگه می‌دارد.

    نسخه‌ی test_bot_admin فقط متن را ذخیره می‌کند؛ این‌جا reply_markup موضوعِ
    اصلی تست است، پس باید دیده شود.
    """

    def __init__(self, text: str = ""):
        self.text = text
        self.sent: list = []
        self.markups: list = []

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)
        self.markups.append(kwargs.get("reply_markup", "absent"))


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

    @property
    def markup(self):
        """آخرین reply_markup — "absent" یعنی اصلاً پاس نشده."""
        return self.effective_message.markups[-1]


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
    monkeypatch.setattr(bot, "PUBLISH_PAUSED", False)

    async def no_refresh(force: bool = False) -> None:
        return None

    monkeypatch.setattr(bot, "refresh_cache", no_refresh)


def run(coro):
    return asyncio.run(coro)


def texts(keyboard: ReplyKeyboardMarkup) -> set:
    return {b.text for row in keyboard.keyboard for b in row}


# ─── رفت و برگشت کلید ─────────────────────────────────────

def test_the_toggle_hides_then_restores_the_keyboard():
    context = FakeContext()

    hide = FakeUpdate(uid=USER)
    run(bot.cmd_keyboard(hide, context))
    assert isinstance(hide.markup, ReplyKeyboardRemove)
    assert bot.keyboard_hidden(context) is True

    show = FakeUpdate(uid=USER)
    run(bot.cmd_keyboard(show, context))
    assert show.markup is bot.MAIN_KEYBOARD
    assert bot.keyboard_hidden(context) is False


def test_the_admin_gets_the_admin_keyboard_back_not_the_plain_one():
    """ادمین بعد از نمایش دوباره نباید ردیف‌های مدیریتی‌اش را از دست بدهد."""
    context = FakeContext()
    run(bot.cmd_keyboard(FakeUpdate(uid=ADMIN), context))
    back = FakeUpdate(uid=ADMIN)
    run(bot.cmd_keyboard(back, context))
    assert back.markup is bot.ADMIN_KEYBOARD


def test_the_hide_message_names_the_way_back():
    """با رفتن کیبورد، دکمه‌ی برگشت هم می‌رود — پس متن باید راه را بگوید."""
    update = FakeUpdate(uid=USER)
    run(bot.cmd_keyboard(update, FakeContext()))
    assert "/keyboard" in update.sent
    assert "/help" in update.sent


def test_the_button_text_reaches_the_same_handler():
    """زدنِ «⌨️ پنهان کردن دکمه‌ها» و تایپ /keyboard باید یکی باشند."""
    context = FakeContext()
    update = FakeUpdate(bot.BTN_KEYBOARD, uid=USER)
    run(bot.handle_button(update, context))
    assert bot.keyboard_hidden(context) is True
    assert isinstance(update.markup, ReplyKeyboardRemove)


# ─── پنهان ماندن ──────────────────────────────────────────

def test_a_hidden_keyboard_is_not_resurrected_by_the_next_reply():
    """قلبِ این فیچر: /help نباید کیبوردِ پنهان‌شده را برگرداند."""
    context = FakeContext()
    run(bot.cmd_keyboard(FakeUpdate(uid=USER), context))

    help_update = FakeUpdate(uid=USER)
    run(bot.cmd_help(help_update, context))
    assert help_update.markup is None          # None = «به کیبورد دست نزن»


def test_help_shows_the_keyboard_when_it_was_never_hidden():
    help_update = FakeUpdate(uid=USER)
    run(bot.cmd_help(help_update, FakeContext()))
    assert help_update.markup is bot.MAIN_KEYBOARD


def test_the_unknown_text_hint_changes_when_buttons_are_hidden():
    """«از دکمه‌های پایین استفاده کن» وقتی دکمه‌ای نیست دروغ است."""
    context = FakeContext()
    run(bot.cmd_keyboard(FakeUpdate(uid=USER), context))

    lost = FakeUpdate("چیز نامفهوم", uid=USER)
    run(bot.handle_button(lost, context))
    assert "/keyboard" in lost.sent
    assert lost.markup == "absent"             # کیبورد دست‌نخورده می‌ماند

    seeing = FakeUpdate("چیز نامفهوم", uid=USER)
    run(bot.handle_button(seeing, FakeContext()))
    assert seeing.markup is bot.MAIN_KEYBOARD


def test_start_is_a_reset_and_brings_the_keyboard_back():
    context = FakeContext()
    run(bot.cmd_keyboard(FakeUpdate(uid=USER), context))
    started = FakeUpdate(uid=USER)
    run(bot.cmd_start(started, context))
    assert bot.keyboard_hidden(context) is False
    assert started.markup is bot.MAIN_KEYBOARD


def test_markup_for_falls_back_to_the_plain_keyboard_without_user_data():
    """کانتکست بدون user_data (مثلاً از channel_post) نباید کرش کند."""
    class NoData:
        user_data = None

    assert bot.keyboard_hidden(NoData()) is False
    assert bot.markup_for(FakeUpdate(uid=USER), NoData()) is bot.MAIN_KEYBOARD


# ─── ثبت دستور ────────────────────────────────────────────

def test_keyboard_is_a_registered_command_not_only_a_button():
    """اگر در USER_COMMANDS نباشد، main() هیچ CommandHandler نمی‌سازد و
    /keyboard — همان راه برگشتی که پیامِ پنهان‌شدن وعده می‌دهد — کار نمی‌کند."""
    names = {name for name, _desc, _fn in bot.USER_COMMANDS}
    assert "keyboard" in names
    assert dict((n, f) for n, _d, f in bot.USER_COMMANDS)["keyboard"] is (
        bot.cmd_keyboard
    )


# ─── دکمه‌ها: مسیر و متن ───────────────────────────────────

def test_every_keyboard_button_has_a_route():
    """دکمه‌ی بی‌مسیر به شاخه‌ی «متن ناشناس» می‌افتد و خراب به نظر می‌رسد."""
    routes = set(bot.BUTTON_ROUTES) | set(bot.ADMIN_ROUTES)
    missing = texts(bot.ADMIN_KEYBOARD) - routes
    assert not missing, f"دکمه‌های بی‌مسیر: {missing}"


def test_every_route_is_reachable_from_some_keyboard():
    """مسیرِ بی‌دکمه کدِ مرده است — یا دکمه‌اش حذف شده یا اسمش عوض شده."""
    orphan = (set(bot.BUTTON_ROUTES) | set(bot.ADMIN_ROUTES)) - texts(
        bot.ADMIN_KEYBOARD
    )
    assert not orphan, f"مسیرهای بی‌دکمه: {orphan}"


def test_admin_only_buttons_stay_out_of_the_user_keyboard():
    user_texts = texts(bot.MAIN_KEYBOARD)
    assert not (set(bot.ADMIN_ROUTES) & user_texts)
    assert bot.BTN_KEYBOARD in user_texts       # کلید برای همه است


def test_button_labels_carry_meaning_without_any_color():
    """کلاینت قدیمی (و PTB قدیمی) رنگ را نشان نمی‌دهد؛ متن باید کافی باشد."""
    for label in texts(bot.ADMIN_KEYBOARD):
        stripped = "".join(ch for ch in label if ch.isalpha() or ch.isspace())
        assert stripped.strip(), f"دکمه‌ی بی‌متن: {label!r}"


def test_no_two_buttons_share_a_label():
    """متن دکمه کلید مسیریابی است؛ تکراری یعنی یکی از دو مسیر گم می‌شود."""
    labels = [b.text for row in bot.ADMIN_KEYBOARD.keyboard for b in row]
    assert len(labels) == len(set(labels))


def test_rows_stay_narrow_enough_for_a_phone():
    for row in bot.ADMIN_KEYBOARD.keyboard:
        assert 1 <= len(row) <= 2
