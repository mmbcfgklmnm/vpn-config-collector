"""تست انتخاب کشور در ربات (`/country` و دکمه‌هایش).

شکایت صریح کاربر: «چرا انتخاب کشور فقط آمریکا است؟ کشورهای بیشتری اضافه کن.»
ریشه‌ی اصلی در لایه ۶ بود (تست آن در tests/test_geo_layer6.py)، ولی نیمه‌ی
دوم این‌جا بود: صفحه‌کلید فقط ۱۲ دکمه می‌ساخت و بقیه‌ی کشورها *فقط* با تایپ
کردن `/country XX` قابل دسترسی بودند. اندازه‌گیری روی داده‌ی واقعی بعد از
اصلاح لایه ۶: ‏۳۸ کشور — یعنی ۲۶ کشور بی‌دکمه می‌ماندند.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
from src import vless

UUID = "11111111-2222-3333-4444-555555555555"
CODES = (
    "CA GB NL US NO DE SG AR JP FI RU FR AU TW SE RS IT PL IN KZ JE ES TR "
    "BG KR HK CZ AT LT MD LV EE UA MY BY AL AE AM"
).split()


class FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.sent: list = []
        self.markups: list = []

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)
        self.markups.append(kwargs.get("reply_markup"))


class FakeUser:
    def __init__(self, uid=7):
        self.id = uid
        self.first_name = "T"


class FakeUpdate:
    def __init__(self, text: str = "", uid: int = 7):
        self.effective_message = FakeMessage(text)
        self.effective_user = FakeUser(uid)

    @property
    def sent(self) -> str:
        return "\n".join(self.effective_message.sent)

    @property
    def markup(self):
        return self.effective_message.markups[-1]


class FakeContext:
    def __init__(self, args=None):
        self.args = args or []
        self.bot = object()
        self.user_data: dict = {}


def cfg(code: str, n: int) -> str:
    raw = f"vless://{UUID}@10.0.{n}.1:443?security=tls&type=tcp#node{n}"
    return vless.add_tag(raw, 100.0 + n, code)


def pool_of(codes) -> list:
    """یک کانفیگ به‌ازای هر کشور، به همان ترتیبِ ورودی."""
    return [cfg(code, n) for n, code in enumerate(codes)]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "BOT_STATE_FILE", str(tmp_path / "bot_state.json"))
    monkeypatch.setattr(bot, "BOT_ENABLED", True)
    monkeypatch.setattr(bot, "ADMIN_IDS", set())

    async def no_refresh(force: bool = False) -> None:
        return None

    monkeypatch.setattr(bot, "refresh_cache", no_refresh)
    monkeypatch.setattr(bot, "load_configs", lambda: pool_of(CODES))


def run(coro):
    return asyncio.run(coro)


def codes_on(markup) -> list:
    return [
        b.callback_data[3:]
        for row in markup.inline_keyboard
        for b in row
        if str(b.callback_data).startswith("co_")
    ]


def pages_on(markup) -> list:
    return [
        b.callback_data
        for row in markup.inline_keyboard
        for b in row
        if str(b.callback_data).startswith("cop_")
    ]


# ─── شمارش ────────────────────────────────────────────────

def test_untagged_and_placeholder_codes_are_not_counted():
    """XX یک کشور نیست؛ شمردنش دکمه‌ای می‌سازد که به هیچ‌جا نمی‌رسد."""
    counts = bot.country_counts(
        pool_of(["DE", "DE", "XX"]) + [f"vless://{UUID}@1.2.3.4:443#plain"]
    )
    assert counts == {"DE": 2}


def test_counts_are_ordered_by_size():
    counts = bot.country_counts(pool_of(["NL", "DE", "DE", "DE", "NL", "SE"]))
    assert list(counts) == ["DE", "NL", "SE"]


# ─── صفحه‌بندی ────────────────────────────────────────────

def test_every_country_is_reachable_by_button():
    """قرارداد اصلی: هیچ کشوری بی‌دکمه نماند."""
    counts = bot.country_counts(pool_of(CODES))
    reachable: list = []
    _, _, pages = bot.country_page(counts)
    for page in range(pages):
        reachable.extend(codes_on(bot.country_keyboard(counts, page)))
    assert sorted(reachable) == sorted(CODES)


def test_pages_do_not_overlap():
    counts = bot.country_counts(pool_of(CODES))
    first = codes_on(bot.country_keyboard(counts, 0))
    second = codes_on(bot.country_keyboard(counts, 1))
    assert len(first) == bot.COUNTRY_PAGE_SIZE
    assert not set(first) & set(second)


def test_navigation_wraps_in_both_directions():
    """کاربری که دکمه را پشت‌سرهم می‌زند به دیوار نمی‌خورد."""
    counts = bot.country_counts(pool_of(CODES))
    _, _, pages = bot.country_page(counts)
    assert bot.country_page(counts, pages)[1] == 0
    assert bot.country_page(counts, -1)[1] == pages - 1


def test_a_short_list_gets_no_navigation_row():
    markup = bot.country_keyboard(bot.country_counts(pool_of(["DE", "NL"])))
    assert pages_on(markup) == []
    assert codes_on(markup) == ["DE", "NL"]


def test_iran_button_is_on_every_page():
    counts = bot.country_counts(pool_of(CODES))
    for page in range(3):
        data = [
            b.callback_data
            for row in bot.country_keyboard(counts, page).inline_keyboard
            for b in row
        ]
        assert "iran" in data


# ─── فرمان و callback ─────────────────────────────────────

def test_command_shows_the_total_not_just_the_page():
    update = FakeUpdate("/country")
    run(bot.cmd_country(update, FakeContext()))
    assert f"{len(CODES)} کشور" in update.sent
    assert "صفحه 1 از" in update.sent
    assert len(codes_on(update.markup)) == bot.COUNTRY_PAGE_SIZE


def test_page_callback_moves_to_the_next_batch():
    first = FakeUpdate()
    run(bot.show_country_list(first, 0))
    second = FakeUpdate()
    run(bot.show_country_list(second, 1))
    assert not set(codes_on(first.markup)) & set(codes_on(second.markup))
    assert "صفحه 2 از" in second.sent


def test_a_stale_page_number_still_lands_on_a_real_page(monkeypatch):
    """صفحه‌کلیدِ کهنه: کاربر «صفحه ۳» را می‌زند ولی پول آب رفته و یک صفحه است.

    عددِ بیرون از بازه چرخانده می‌شود، نه بریده — همان چرخشی که دکمه‌های
    ◀️/▶️ به آن تکیه دارند (‏-۱ → صفحه‌ی آخر). چیزی که این‌جا قفل می‌شود:
    هر عددی که برسد، یک صفحه‌ی واقعی و پرِ دکمه برمی‌گردد، نه خطا و نه
    صفحه‌کلید خالی.
    """
    monkeypatch.setattr(bot, "load_configs", lambda: pool_of(["DE", "NL"]))
    update = FakeUpdate()
    run(bot.show_country_list(update, 3))
    assert codes_on(update.markup) == ["DE", "NL"]
    assert "صفحه" not in update.sent          # یک صفحه = بی‌شماره‌ی صفحه


def test_any_page_number_stays_inside_the_range():
    counts = bot.country_counts(pool_of(CODES))
    _, _, pages = bot.country_page(counts)
    for asked in (-99, -1, 0, 3, 999, 10 ** 9):
        shown, page, _ = bot.country_page(counts, asked)
        assert 0 <= page < pages and shown


def test_empty_pool_says_so_instead_of_an_empty_keyboard(monkeypatch):
    monkeypatch.setattr(bot, "load_configs", lambda: [])
    update = FakeUpdate()
    run(bot.show_country_list(update))
    assert "کانفیگی با برچسب کشور نداریم" in update.sent
    assert update.markup is None


def test_picking_a_country_filters_to_it():
    update = FakeUpdate()
    run(bot.show_country(update, "de"))
    assert "DE" in update.sent
    assert "node5" in update.sent          # DE ششمین کد لیست است


def test_a_bogus_country_code_is_rejected():
    update = FakeUpdate()
    run(bot.show_country(update, "../etc"))
    assert "کد کشور دو حرف انگلیسی" in update.sent
