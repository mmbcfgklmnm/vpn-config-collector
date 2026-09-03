"""تست کارت مشخصات برای سنجه‌های تازه: پایداری، لرزش، سرعت، احیا.

قاعده‌ی مرکزی این فایل: کارت فقط چیزی را می‌گوید که اندازه‌گیری شده. کانفیگ
بی‌برچسب نباید «پایدار» به نظر برسد و نه «ناپایدار» — سکوت درست‌ترین جواب است.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import clean_ip, vless
from src.publisher import renderer

UUID = "11111111-2222-3333-4444-555555555555"
CDN = (
    f"vless://{UUID}@104.16.5.9:443?type=ws&security=tls"
    "&host=cdn.example.com&path=%2Fws#MyNode"
)


def tagged(**kwargs) -> str:
    return vless.add_tag(CDN, 118.0, "DE", 205.0, **kwargs)


# ─── پایداری ──────────────────────────────────────────────

def test_unmeasured_stability_says_nothing():
    text = "\n".join(renderer.spec_lines(tagged()))
    assert "پایداری" not in text
    assert "سرعت دانلود" not in text
    assert renderer.stability_text(-1.0, -1.0) == ""


def test_zero_loss_is_reported_as_stable_with_jitter():
    line = renderer.stability_text(0.0, 9.0)
    assert "بدون افت بسته" in line and "لرزش 9ms" in line


def test_high_loss_is_reported_as_unstable():
    assert "ناپایدار" in renderer.stability_text(22.0, 41.0)
    assert "افت جزئی" in renderer.stability_text(12.0)


def test_stability_reaches_the_card():
    text = "\n".join(renderer.spec_lines(tagged(loss_pct=0.0, jitter_ms=9.0)))
    assert "📶 پایداری: 🟢 پایدار — بدون افت بسته" in text


# ─── سرعت ─────────────────────────────────────────────────

def test_speed_switches_unit_at_one_megabyte():
    assert renderer.speed_text(88.0) == "88 KB/s"
    assert renderer.speed_text(1536.0) == "1.5 MB/s"
    assert renderer.speed_text(0.0) == ""


def test_speed_reaches_the_card():
    assert "⬇️ سرعت دانلود: 430 KB/s" in "\n".join(
        renderer.spec_lines(tagged(speed_kbps=430.0))
    )


# ─── نشانه‌ی احیا ─────────────────────────────────────────

def test_revived_config_explains_its_ip_address():
    """آدرسِ IP روی کانفیگ CDN گیج‌کننده است؛ کارت دلیلش را می‌گوید."""
    revived = vless.add_tag(clean_ip.revive(CDN, "172.67.1.2"), 118.0, "DE", 205.0)
    spec = renderer.describe(revived)
    assert spec["revived"] is True
    assert "♻️ ورودی احیاشده" in "\n".join(renderer.spec_lines(revived))


def test_untouched_config_has_no_revive_note():
    assert renderer.describe(tagged())["revived"] is False
    assert "احیاشده" not in "\n".join(renderer.spec_lines(tagged()))


# ─── خط خلاصه ─────────────────────────────────────────────

def test_one_line_shows_stability_and_speed_compactly():
    line = renderer.one_line(tagged(loss_pct=0.0, jitter_ms=9.0, speed_kbps=1536.0))
    assert "🟢" in line and "1.5 MB/s" in line and "🇮🇷" in line


def test_one_line_names_the_loss_when_there_is_any():
    line = renderer.one_line(tagged(loss_pct=22.0, speed_kbps=88.0))
    assert "P22%" in line and "🟢" not in line


def test_one_line_of_an_untested_config_stays_quiet():
    line = renderer.one_line(tagged())
    assert "P" not in line.split("—")[0].replace("`", "")
    assert "KB/s" not in line
