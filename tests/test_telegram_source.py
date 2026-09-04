"""تست منبع تلگرام — کانال‌های عمومی بدون حساب.

شکایت صریح کاربر: «منابع را به گیت‌هاب محدود نکن؛ کانال تلگرام هم اضافه
کن.» کد قبلی کانال داشت ولی هیچ‌وقت اجرا نمی‌شد (شرط اولش TELEGRAM_SESSION
بود و در health.json هیچ رکورد telegram ای نبود).

این‌جا سه چیز قفل می‌شود، هر سه با HTMLِ واقعیِ t.me:
  ۱. `&amp;` باید unescape شود، وگرنه کانفیگِ استخراج‌شده در کلاینت باز نمی‌شود.
  ۲. `<br/>` مرزِ دو کانفیگ در یک پیام است، وگرنه هر دو خراب می‌شوند.
  ۳. کانالِ بی‌پیش‌نمایش هم ۲۰۰ می‌دهد — «مرده» با شمردن پیام تشخیص داده شود.

بدون شبکه اجرا می‌شود.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import health
from src.scraper import telegram_scraper as tg

UUID = "11111111-2222-3333-4444-555555555555"


def link(host: str, name: str = "n") -> str:
    """کانفیگِ سالم، همان شکلی که کاربر در کلاینت می‌چسباند."""
    return f"vless://{UUID}@{host}:443?security=tls&type=tcp#{name}"


def escaped(config: str) -> str:
    """همان کانفیگ، همان‌طور که تلگرام در HTML می‌گذارد."""
    return config.replace("&", "&amp;")


def message(body: str, post: str = "chan/100") -> str:
    """یک پیام با همان کلاس‌ها و ساختارِ صفحه‌ی واقعی."""
    return (
        f'<div class="tgme_widget_message" data-post="{post}">'
        f'<div class="tgme_widget_message_text js-message_text" dir="auto">'
        f"{body}</div>"
        f'<div class="tgme_widget_message_footer compact">'
        f'<a class="tgme_widget_message_date" href="https://t.me/{post}">'
        f"<time>2026-09-03</time></a></div></div>"
    )


def page(*messages: str) -> str:
    return f'<main><section class="tgme_channel_history">{"".join(messages)}</section></main>'


NO_PREVIEW = (
    '<html><head><title>Telegram: Contact @x</title></head>'
    '<body><div class="tgme_page">Contact @x</div></body></html>'
)


class FakeResponse:
    def __init__(self, status: int, text: str):
        self.status = status
        self._text = text

    async def text(self, **kwargs):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """جای aiohttp.ClientSession — هر URL یک صفحه‌ی آماده."""

    def __init__(self, pages, status: int = 200):
        self.pages = list(pages)
        self.status = status
        self.urls: list = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        text = self.pages.pop(0) if self.pages else page()
        return FakeResponse(self.status, text)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    health.reset()
    monkeypatch.setattr(tg, "PAGE_PAUSE_SEC", 0)
    monkeypatch.setattr(tg, "TELEGRAM_PAGES", 1)
    yield
    health.reset()


def run(coro):
    return asyncio.run(coro)


def dead_names() -> list:
    return [d["name"] for d in health.snapshot()["dead_sources"]]


# ─── خواندن HTML ──────────────────────────────────────────

def test_html_entities_are_decoded_back_into_the_config():
    """`&amp;` اگر بماند، پارامترها به هم می‌چسبند و کانفیگ بی‌مصرف است."""
    config = link("1.2.3.4")
    found = tg.configs_in_page(page(message(f"<code>{escaped(config)}</code>")))
    assert found == [config]
    assert "&amp;" not in found[0]


def test_a_br_between_two_configs_is_a_real_boundary():
    """دو کانفیگ در یک پیام با <br/> جدا می‌شوند، نه با خط جدید."""
    first, second = link("1.2.3.4", "a"), link("5.6.7.8", "b")
    body = f"<code>{escaped(first)}<br/>{escaped(second)}</code>"
    assert tg.configs_in_page(page(message(body))) == [first, second]


def test_a_base64_message_is_decoded():
    """بعضی کانال‌ها بلوکِ Base64 می‌گذارند، نه لینکِ خام."""
    import base64
    config = link("9.9.9.9")
    blob = base64.b64encode(config.encode()).decode()
    assert tg.configs_in_page(page(message(f"<code>{blob}</code>"))) == [config]


def test_link_preview_is_not_mistaken_for_a_message():
    """کانفیگِ داخل پیش‌نمایشِ لینک، دوباره‌شماری است."""
    config = link("1.2.3.4")
    body = (
        f"<code>{escaped(config)}</code></div>"
        f'<a class="tgme_widget_message_link_preview">'
        f"{escaped(link('7.7.7.7', 'preview'))}</a>"
    )
    assert tg.configs_in_page(page(message(body))) == [config]


def test_duplicates_inside_one_page_collapse():
    config = link("1.2.3.4")
    body = escaped(config)
    assert tg.configs_in_page(page(message(body), message(body, "chan/101"))) == [config]


def test_message_ids_come_from_data_post():
    html_page = page(message("x", "chan/50"), message("y", "chan/51"))
    assert tg.message_ids(html_page) == [50, 51]


def test_a_page_without_messages_yields_nothing():
    assert tg.configs_in_page(NO_PREVIEW) == []
    assert tg.message_ids(NO_PREVIEW) == []


# ─── یک کانال ─────────────────────────────────────────────

def test_a_channel_without_a_public_preview_is_reported_dead():
    """۲۰۰ گرفتن دلیل زنده بودن نیست — پنج کانالِ فهرست قبلی همین بودند."""
    session = FakeSession([NO_PREVIEW])
    assert run(tg.fetch_channel(session, "gone")) == []
    assert dead_names() == ["gone"]
    assert health.snapshot()["dead_sources"][0]["error"] == "پیش‌نمایش عمومی ندارد"


def test_an_http_error_is_recorded_with_its_code():
    session = FakeSession([""], status=429)
    assert run(tg.fetch_channel(session, "throttled")) == []
    assert health.snapshot()["dead_sources"][0]["error"] == "HTTP 429"


def test_a_live_channel_is_recorded_with_its_count():
    session = FakeSession([page(message(escaped(link("1.2.3.4"))))])
    assert len(run(tg.fetch_channel(session, "alive"))) == 1
    assert dead_names() == []
    assert health.snapshot()["by_kind"]["telegram"]["configs"] == 1


def test_paging_walks_backwards_from_the_oldest_message(monkeypatch):
    """?before=<کوچک‌ترین id> — بدونش همان ۲۰ پیام آخر بی‌پایان تکرار می‌شود."""
    monkeypatch.setattr(tg, "TELEGRAM_PAGES", 3)
    new, old = link("1.1.1.1", "new"), link("2.2.2.2", "old")
    session = FakeSession([
        page(message(escaped(new), "chan/200"), message("x", "chan/201")),
        page(message(escaped(old), "chan/180")),
        page(),                      # بی‌پیام: انتهای کانال
    ])
    assert run(tg.fetch_channel(session, "chan")) == [new, old]
    assert session.urls == [
        "https://t.me/s/chan",
        "https://t.me/s/chan?before=200",
        "https://t.me/s/chan?before=180",
    ]


def test_paging_stops_when_the_page_does_not_move(monkeypatch):
    """اگر t.me همان صفحه را برگرداند، حلقه باید بایستد نه اینکه بچرخد."""
    monkeypatch.setattr(tg, "TELEGRAM_PAGES", 5)
    same = page(message(escaped(link("1.2.3.4")), "chan/300"))
    session = FakeSession([same, same, same, same, same])
    assert len(run(tg.fetch_channel(session, "chan"))) == 1
    assert len(session.urls) == 2


def test_the_end_of_a_channel_is_not_an_error(monkeypatch):
    """صفحه‌ی دومِ خالی یعنی کانال ته کشید، نه اینکه منبع مرده باشد."""
    monkeypatch.setattr(tg, "TELEGRAM_PAGES", 3)
    session = FakeSession([
        page(message(escaped(link("1.2.3.4")), "chan/9")),
        NO_PREVIEW,
    ])
    assert len(run(tg.fetch_channel(session, "chan"))) == 1
    assert dead_names() == []


def test_a_channel_cannot_exceed_the_per_source_cap(monkeypatch):
    monkeypatch.setattr(tg, "MAX_PER_SOURCE", 2)
    monkeypatch.setattr(tg, "TELEGRAM_PAGES", 4)
    body = "<br/>".join(escaped(link(f"1.2.3.{n}", f"n{n}")) for n in range(9))
    session = FakeSession([page(message(body, "chan/70"))])
    assert len(run(tg.fetch_channel(session, "chan"))) == 2


# ─── همه‌ی کانال‌ها ───────────────────────────────────────

def test_every_channel_is_visited_and_results_are_merged(monkeypatch):
    monkeypatch.setattr(tg, "TELEGRAM_CHANNELS", ["one", "two"])
    seen: list = []

    async def fake_channel(session, channel):
        seen.append(channel)
        return [link("1.2.3.4", channel)]

    monkeypatch.setattr(tg, "fetch_channel", fake_channel)
    got = run(tg.scrape_telegram_web())
    assert sorted(seen) == ["one", "two"]
    assert len(got) == 2


def test_one_broken_channel_does_not_sink_the_rest(monkeypatch):
    """gather با return_exceptions — یک استثنا نباید کل منبع را صفر کند."""
    monkeypatch.setattr(tg, "TELEGRAM_CHANNELS", ["bad", "good"])

    async def fake_channel(session, channel):
        if channel == "bad":
            raise RuntimeError("boom")
        return [link("1.2.3.4")]

    monkeypatch.setattr(tg, "fetch_channel", fake_channel)
    assert len(run(tg.scrape_telegram_web())) == 1


def test_an_empty_channel_list_asks_nothing(monkeypatch):
    monkeypatch.setattr(tg, "TELEGRAM_CHANNELS", [])
    assert run(tg.scrape_telegram_web()) == []


# ─── مسیر دوم (Telethon) ─────────────────────────────────

def test_missing_credentials_skip_telethon_without_failing(monkeypatch):
    """قبلاً نبودِ session یعنی صفر کانفیگ. حالا فقط مسیر دوم رد می‌شود."""
    monkeypatch.setattr(tg, "TELEGRAM_SESSION", "")
    monkeypatch.setattr(tg, "TELEGRAM_API_ID", "")
    monkeypatch.setattr(tg, "TELEGRAM_API_HASH", "")
    called = []

    async def fake_web():
        return [link("1.2.3.4")]

    async def fake_api():
        called.append(True)
        return []

    monkeypatch.setattr(tg, "scrape_telegram_web", fake_web)
    monkeypatch.setattr(tg, "scrape_telegram_api", fake_api)
    assert len(run(tg.scrape_telegram())) == 1
    assert called == []


def test_credentials_add_the_api_path_on_top(monkeypatch):
    monkeypatch.setattr(tg, "TELEGRAM_SESSION", "s3cr3t-not-printed")
    monkeypatch.setattr(tg, "TELEGRAM_API_ID", "1")
    monkeypatch.setattr(tg, "TELEGRAM_API_HASH", "h")

    async def fake_web():
        return [link("1.2.3.4", "web")]

    async def fake_api():
        return [link("5.6.7.8", "api")]

    monkeypatch.setattr(tg, "scrape_telegram_web", fake_web)
    monkeypatch.setattr(tg, "scrape_telegram_api", fake_api)
    assert len(run(tg.scrape_telegram())) == 2


def test_telethon_failure_never_leaks_the_session(monkeypatch, caplog):
    """رشته‌ی session دسترسی کامل به حساب است؛ در هیچ لاگی نباید بیاید."""
    secret = "1BVtsOKUBu0ndoNotPrintThis"
    monkeypatch.setattr(tg, "TELEGRAM_SESSION", secret)
    monkeypatch.setattr(tg, "TELEGRAM_API_ID", "1")
    monkeypatch.setattr(tg, "TELEGRAM_API_HASH", "hash-also-secret")
    with caplog.at_level("DEBUG"):
        assert run(tg.scrape_telegram_api()) == []
    assert secret not in caplog.text
    assert "hash-also-secret" not in caplog.text
