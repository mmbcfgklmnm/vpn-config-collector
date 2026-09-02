"""تست چرخش انتشار (src/publisher/publisher.py).

چرا مهم است: با انتشار هر ۵ دقیقه و بدون حافظه، همان ۱۰ کانفیگِ کم‌تأخیر
ثابت تکرار می‌شدند و بقیه‌ی پول هیچ‌وقت دیده نمی‌شد.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import vless
from src.publisher import publisher as pub

UUID = "11111111-2222-3333-4444-555555555555"


def cfg(n: int, latency: float = 100.0, iran: float = 0.0) -> str:
    raw = f"vless://{UUID}@10.0.0.{n}:443?security=tls&type=tcp#node{n}"
    return vless.add_tag(raw, latency, "NL", iran)


FAST_PLAIN = cfg(1, 20.0)
SLOW_IRAN = cfg(2, 400.0, iran=190.0)
MID_PLAIN = cfg(3, 90.0)


# ─── ترتیب ────────────────────────────────────────────────

def test_iran_verified_beats_lower_latency():
    """تأییدشده‌ی ایران با ۴۰۰ms جلوتر از کانفیگ ۲۰ms بدون تأیید است."""
    assert pub.rank_key(SLOW_IRAN) < pub.rank_key(FAST_PLAIN)
    ordered = pub.select_for_publish(
        [FAST_PLAIN, MID_PLAIN, SLOW_IRAN], {"cycle": 0, "posted": {}}, count=3
    )
    assert ordered == [SLOW_IRAN, FAST_PLAIN, MID_PLAIN]


def test_duplicates_collapse():
    chosen = pub.select_for_publish(
        [FAST_PLAIN, FAST_PLAIN], {"cycle": 0, "posted": {}}, count=2
    )
    assert chosen == [FAST_PLAIN]


def test_empty_pool_returns_empty():
    assert pub.select_for_publish([], {"cycle": 0, "posted": {}}, count=5) == []


# ─── چرخش ─────────────────────────────────────────────────

def test_cooldown_rotates_through_pool(monkeypatch):
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN", 2)
    pool = [FAST_PLAIN, MID_PLAIN, SLOW_IRAN]

    state = {"cycle": 0, "posted": {}}
    seen = []
    for _ in range(4):
        chosen = pub.select_for_publish(pool, state, count=1)
        seen.append(vless.short_id(chosen[0]))
        state = pub.mark_published(state, chosen)

    # سه کانفیگ متفاوت، و در دوره‌ی چهارم برگشت به اولی
    assert len(set(seen[:3])) == 3
    assert seen[3] == seen[0]


def test_falls_back_to_oldest_when_all_in_cooldown(monkeypatch):
    """پول کوچک‌تر از count×cooldown — کانال نباید خالی بماند."""
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN", 10)
    pool = [FAST_PLAIN, MID_PLAIN]
    state = {
        "cycle": 5,
        "posted": {
            vless.short_id(FAST_PLAIN): 2,   # قدیمی‌تر
            vless.short_id(MID_PLAIN): 4,
        },
    }
    chosen = pub.select_for_publish(pool, state, count=1)
    assert chosen == [FAST_PLAIN]


def test_fresh_configs_come_before_stale_ones(monkeypatch):
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN", 10)
    pool = [SLOW_IRAN, FAST_PLAIN]
    state = {"cycle": 1, "posted": {vless.short_id(SLOW_IRAN): 1}}
    # SLOW_IRAN رتبه‌ی بالاتری دارد ولی در cooldown است
    assert pub.select_for_publish(pool, state, count=2) == [FAST_PLAIN, SLOW_IRAN]


def test_mark_published_advances_cycle():
    state = pub.mark_published({"cycle": 7, "posted": {}}, [FAST_PLAIN])
    assert state["cycle"] == 8
    assert state["posted"][vless.short_id(FAST_PLAIN)] == 8
    assert state["last_count"] == 1
    assert state["last_publish"]


def test_mark_published_prunes_ancient_ids(monkeypatch):
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN", 2)
    # horizon = (cycle+1) - 2*4 = 93 → شناسه‌ی دوره‌ی ۱ باید حذف شود
    state = pub.mark_published({"cycle": 99, "posted": {"DEAD": 1}}, [FAST_PLAIN])
    assert "DEAD" not in state["posted"]
    assert vless.short_id(FAST_PLAIN) in state["posted"]


# ─── وضعیت روی دیسک ───────────────────────────────────────

def test_state_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "publish_state.json"
    monkeypatch.setattr(pub, "PUBLISH_STATE_FILE", str(path))
    pub.save_state({"cycle": 3, "posted": {"AAAA": 3}})
    assert pub.load_state() == {"cycle": 3, "posted": {"AAAA": 3}}
    assert b"\r\n" not in path.read_bytes()


def test_load_state_tolerates_missing_and_corrupt(tmp_path, monkeypatch):
    path = tmp_path / "publish_state.json"
    monkeypatch.setattr(pub, "PUBLISH_STATE_FILE", str(path))
    assert pub.load_state() == {"cycle": 0, "posted": {}}

    path.write_text("{not json", encoding="utf-8")
    assert pub.load_state() == {"cycle": 0, "posted": {}}

    # نوع اشتباه هم نباید کرش کند
    path.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert pub.load_state() == {"cycle": 0, "posted": {}}
    path.write_text(json.dumps({"cycle": 4, "posted": "nope"}), encoding="utf-8")
    assert pub.load_state() == {"cycle": 4, "posted": {}}


def test_save_state_survives_unwritable_path(monkeypatch, tmp_path):
    """نبودِ حافظه‌ی چرخش نباید انتشار را متوقف کند."""
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(pub, "PUBLISH_STATE_FILE", str(blocker / "state.json"))
    pub.save_state({"cycle": 1, "posted": {}})  # نباید استثنا بدهد


# ─── پیام اشتراک ──────────────────────────────────────────

def test_sub_message_has_counts_and_link():
    text = pub.build_sub_message(128, 44)
    assert "*128*" in text
    assert "*44*" in text
    assert "#vless" in text
    if pub.SUB_URL:
        assert pub.SUB_URL in text


def test_sub_message_hides_iran_line_when_zero():
    assert "تأییدشده از ایران" not in pub.build_sub_message(10, 0)


def test_sub_message_fits_telegram_limit():
    assert len(pub.build_sub_message(9999, 9999)) <= 3800
