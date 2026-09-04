"""تست دکمه‌های پایین صفحه: کنترلِ پنهان/نمایش، نقش‌ها، متن و مسیر.

شکایت کاربر: «آن چیزی نیست که از Bot Keyboard Toggle منظورم بود؛ یک آیکن
شبکه/کیبورد کنار کادر نوشتن است، فیچر خودِ تلگرام.» درست بود — و علتش یک
پارامتر بود. مستند Bot API برای `is_persistent`:

    «Requests clients to always show the keyboard when the regular keyboard
    is hidden. Defaults to False, in which case the custom keyboard can be
    hidden and opened with a keyboard icon.»

یعنی `is_persistent=True` *دقیقاً همان آیکن را حذف می‌کند*. نسخه‌ی قبلی True
می‌فرستاد و بعد با /keyboard و یک دکمه‌ی خودساخته جایش را پر می‌کرد. حالا
پاس نمی‌شود، پس کنترل همان آیکنِ بومیِ کنار کادر نوشتن است.

سه چیز این‌جا قفل می‌شود:
  ۱. هیچ کیبوردی `is_persistent` نمی‌فرستد — تنها شرطِ بودنِ آن آیکن.
  ۲. کیبورد ادمین دکمه‌ی کاربر ندارد (خواسته‌ی دومِ کاربر).
  ۳. هیچ متنی /keyboard را وعده نمی‌دهد — آن دستور دیگر وجود ندارد.

رنگ‌ها روی محیط محلی قابل تست نیستند (PTB 21.1.1 پارامتر style را نمی‌شناسد و
tg_ui آن را نمی‌فرستد تا کرش نکند)، پس فقط چیزی تست می‌شود که در هر دو نسخه
درست است: متن هر دکمه به‌تنهایی گویا باشد و مسیر داشته باشد — رنگ تأکید است،
تنها حامل معنا نیست.
"""
import asyncio
import os
import sys

import pytest
from telegram import ReplyKeyboardMarkup

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


KEYBOARDS = (bot.MAIN_KEYBOARD, bot.ADMIN_KEYBOARD)


def texts(*keyboards: ReplyKeyboardMarkup) -> set:
    return {b.text for kb in keyboards for row in kb.keyboard for b in row}


# ─── آیکنِ خودِ تلگرام ─────────────────────────────────────

@pytest.mark.parametrize("keyboard", KEYBOARDS)
def test_no_keyboard_asks_to_be_persistent(keyboard):
    """قلبِ این فیچر: is_persistent نباید در payload باشد.

    هر مقدارِ صریحی — حتی False — لازم نیست؛ چیزی که خرابی می‌سازد True است.
    این تست روی to_dict نگاه می‌کند نه روی attribute، چون همان دیکشنری است که
    واقعاً به تلگرام می‌رود و همان است که آیکن را می‌آورد یا می‌برد.
    """
    assert "is_persistent" not in keyboard.to_dict()
    assert not keyboard.is_persistent


@pytest.mark.parametrize("keyboard", KEYBOARDS)
def test_the_keyboard_still_resizes_and_hints(keyboard):
    """resize_keyboard نبود یعنی ۵ ردیف دکمه نصف صفحه‌ی گوشی را می‌گیرد."""
    assert keyboard.resize_keyboard is True
    assert (keyboard.input_field_placeholder or "").strip()


def test_nothing_promises_the_removed_keyboard_command():
    """دستور /keyboard حذف شد؛ متنی که وعده‌اش را بدهد کاربر را سرگردان می‌کند."""
    assert not hasattr(bot, "cmd_keyboard")
    assert "keyboard" not in {name for name, _d, _f in bot.USER_COMMANDS}

    for handler in (bot.cmd_start, bot.cmd_help):
        update = FakeUpdate(uid=USER)
        run(handler(update, FakeContext()))
        assert "/keyboard" not in update.sent


# ─── نقش‌ها: کیبورد ادمین ≠ کیبورد کاربر ──────────────────

def test_the_admin_keyboard_holds_no_user_buttons():
    """خواسته‌ی کاربر: «دکمه‌های مخصوص کاربر نباید برای ادمین دیده شوند.»"""
    assert not (texts(bot.ADMIN_KEYBOARD) & texts(bot.MAIN_KEYBOARD))
    assert texts(bot.ADMIN_KEYBOARD) == set(bot.ADMIN_ROUTES)


def test_the_user_keyboard_holds_no_admin_buttons():
    """جهت دیگرِ همان مرز — دکمه‌ی ادمین در کیبورد کاربر یعنی «⛔ فقط ادمین»."""
    assert not (set(bot.ADMIN_ROUTES) & texts(bot.MAIN_KEYBOARD))


def test_keyboard_for_picks_by_role():
    assert bot.keyboard_for(ADMIN) is bot.ADMIN_KEYBOARD
    assert bot.keyboard_for(USER) is bot.MAIN_KEYBOARD
    assert bot.keyboard_for(None) is bot.MAIN_KEYBOARD


@pytest.mark.parametrize("uid,expected", [(USER, 0), (ADMIN, 1)])
def test_start_and_help_carry_the_keyboard_of_the_role(uid, expected):
    for handler in (bot.cmd_start, bot.cmd_help):
        update = FakeUpdate(uid=uid)
        run(handler(update, FakeContext()))
        assert update.markup is KEYBOARDS[expected]


@pytest.mark.parametrize("uid,expected", [(USER, 0), (ADMIN, 1)])
def test_an_unknown_text_brings_the_buttons_back(uid, expected):
    """اگر کاربر با آیکنِ تلگرام دکمه‌ها را بسته باشد، ربات خبر ندارد.

    پس پاسخِ متنِ ناشناس کیبورد را دوباره می‌چسباند: همین راهِ برگشت است و
    هزینه‌ای هم ندارد — کسی که دکمه نمی‌خواهد باز با همان آیکن می‌بندد.
    """
    update = FakeUpdate("چیز نامفهوم", uid=uid)
    run(bot.handle_button(update, FakeContext()))
    assert update.markup is KEYBOARDS[expected]
    assert "/help" in update.sent


# ─── دکمه‌ها: مسیر و متن ───────────────────────────────────

def test_every_keyboard_button_has_a_route():
    """دکمه‌ی بی‌مسیر به شاخه‌ی «متن ناشناس» می‌افتد و خراب به نظر می‌رسد."""
    routes = set(bot.BUTTON_ROUTES) | set(bot.ADMIN_ROUTES)
    missing = texts(*KEYBOARDS) - routes
    assert not missing, f"دکمه‌های بی‌مسیر: {missing}"


def test_every_route_is_reachable_from_some_keyboard():
    """مسیرِ بی‌دکمه کدِ مرده است — یا دکمه‌اش حذف شده یا اسمش عوض شده.

    «some» یعنی اجتماعِ دو کیبورد: از وقتی ادمین ردیف‌های کاربر را نمی‌بیند،
    هیچ کیبوردی به‌تنهایی همه‌ی مسیرها را ندارد.
    """
    orphan = (set(bot.BUTTON_ROUTES) | set(bot.ADMIN_ROUTES)) - texts(*KEYBOARDS)
    assert not orphan, f"مسیرهای بی‌دکمه: {orphan}"


def test_button_labels_carry_meaning_without_any_color():
    """کلاینت قدیمی (و PTB قدیمی) رنگ را نشان نمی‌دهد؛ متن باید کافی باشد."""
    for label in texts(*KEYBOARDS):
        stripped = "".join(ch for ch in label if ch.isalpha() or ch.isspace())
        assert stripped.strip(), f"دکمه‌ی بی‌متن: {label!r}"


def test_no_two_buttons_share_a_label():
    """متن دکمه کلید مسیریابی است؛ تکراری یعنی یکی از دو مسیر گم می‌شود."""
    labels = [b.text for kb in KEYBOARDS for row in kb.keyboard for b in row]
    assert len(labels) == len(set(labels))


@pytest.mark.parametrize("keyboard", KEYBOARDS)
def test_rows_stay_narrow_enough_for_a_phone(keyboard):
    for row in keyboard.keyboard:
        assert 1 <= len(row) <= 2
