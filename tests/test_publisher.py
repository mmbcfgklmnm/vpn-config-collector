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


# ─── سهمیه‌ی سخت‌گیرانه ────────────────────────────────────

RESERVE_A = cfg(11, 55.0)
RESERVE_B = cfg(12, 65.0)


def test_international_fills_quota_when_iran_pool_is_small():
    """مشکل گزارش‌شده: دوره‌هایی با ۳ کانفیگ چون فقط ایرانی‌ها پست می‌شدند."""
    pool = [SLOW_IRAN, FAST_PLAIN, MID_PLAIN]
    chosen = pub.select_for_publish(pool, {"cycle": 0, "posted": {}}, count=3)
    assert len(chosen) == 3
    assert chosen[0] == SLOW_IRAN          # ایرانی اول
    assert set(chosen[1:]) == {FAST_PLAIN, MID_PLAIN}   # ولی بقیه هم می‌آیند


def test_reserve_fills_remaining_quota():
    chosen = pub.select_for_publish(
        [SLOW_IRAN], {"cycle": 0, "posted": {}}, count=3,
        reserve=[RESERVE_B, RESERVE_A],
    )
    # تأییدشده اول، بعد ذخیره به ترتیب تأخیر
    assert chosen == [SLOW_IRAN, RESERVE_A, RESERVE_B]


def test_reserve_is_not_used_when_verified_pool_suffices():
    chosen = pub.select_for_publish(
        [SLOW_IRAN, FAST_PLAIN], {"cycle": 0, "posted": {}}, count=2,
        reserve=[RESERVE_A],
    )
    assert RESERVE_A not in chosen


def test_fresh_reserve_beats_stale_verified(monkeypatch):
    """تکرار کانفیگی که تازه پست شده برای کاربر چیز تازه‌ای ندارد."""
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN", 10)
    state = {"cycle": 1, "posted": {vless.short_id(SLOW_IRAN): 1}}
    chosen = pub.select_for_publish(
        [SLOW_IRAN], state, count=2, reserve=[RESERVE_A],
    )
    assert chosen == [RESERVE_A, SLOW_IRAN]


def test_reserve_duplicate_of_verified_config_counts_once():
    """یک endpoint می‌تواند در هر دو پول باشد؛ سهمیه را دو بار نمی‌خورد."""
    same_endpoint_untested = vless.add_tag(
        f"vless://{UUID}@10.0.0.2:443?security=tls&type=tcp#node2", 900.0, "NL", 0.0
    )
    chosen = pub.select_for_publish(
        [SLOW_IRAN], {"cycle": 0, "posted": {}}, count=5,
        reserve=[same_endpoint_untested],
    )
    assert chosen == [SLOW_IRAN]


def test_reserve_ids_marks_only_untested_endpoints():
    ids = pub.reserve_ids([SLOW_IRAN], [RESERVE_A, SLOW_IRAN])
    assert ids == {vless.short_id(RESERVE_A)}
    assert pub.reserve_ids([SLOW_IRAN], None) == set()


def test_quota_cannot_be_padded_with_duplicates():
    """اگر پول واقعاً کوچک است، عدد کم برمی‌گردد — کانفیگ جعلی ساخته نمی‌شود."""
    chosen = pub.select_for_publish(
        [FAST_PLAIN], {"cycle": 0, "posted": {}}, count=10, reserve=[FAST_PLAIN],
    )
    assert chosen == [FAST_PLAIN]


def test_zero_count_returns_empty():
    assert pub.select_for_publish([FAST_PLAIN], {"cycle": 0, "posted": {}}, count=-1) == []


# ─── کارت‌ها ──────────────────────────────────────────────

def test_pool_card_never_claims_it_was_tested():
    """کارت ذخیره نباید ادعای «در ۳ دور تست شد» داشته باشد."""
    from src.publisher import renderer
    card = renderer.spec_card(RESERVE_A, 1, 10, verified_rounds=3, badge="pool")
    assert "تست‌نشده" in card
    assert "دور پشت سر هم" not in card
    assert RESERVE_A in card


def test_donated_card_is_labelled():
    from src.publisher import renderer
    card = renderer.spec_card(RESERVE_A, 1, 12, verified_rounds=3, badge="donated")
    assert "اهدایی" in card
    assert "دور پشت سر هم" not in card


def test_verified_card_keeps_rounds_claim():
    from src.publisher import renderer
    card = renderer.spec_card(SLOW_IRAN, 1, 10, verified_rounds=3)
    assert "در 3 دور پشت سر هم تست شد" in card


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


def test_sub_message_splits_domestic_and_international(monkeypatch):
    """خواسته‌ی کاربر: انتهای مسیر اشتراک، داخلی و خارجی از هم جدا باشند."""
    monkeypatch.setattr(pub, "SUB_IRAN_URL", "https://x/iran.txt")
    monkeypatch.setattr(pub, "SUB_INTL_URL", "https://x/international.txt")
    text = pub.build_sub_message(100, iran_count=40, intl_count=60)
    assert "https://x/iran.txt" in text
    assert "https://x/international.txt" in text
    assert "*40*" in text and "*60*" in text


def test_sub_message_hides_intl_link_when_none(monkeypatch):
    monkeypatch.setattr(pub, "SUB_INTL_URL", "https://x/international.txt")
    assert "https://x/international.txt" not in pub.build_sub_message(10, 10, 0)


# ─── انتظارِ خودتنظیم ─────────────────────────────────────
#
# شکایت صریح کاربر: «کانفیگ‌هایی که قبلاً کار کرده‌اند نباید دوباره و دوباره
# فرستاده شوند.» با انتظارِ ثابتِ ۶ دوره و پول ۷۲۳ تایی، بهترین کانفیگ
# نیم‌ساعت بعد دوباره واجد شرط می‌شد و چون سرِ صف rank_key بود همان لحظه
# انتخاب می‌شد؛ نتیجه گردش ابدی روی ~۶۰ کانفیگ اول بود.

def test_cooldown_scales_with_pool_size():
    """۷۲۰ کانفیگ با ۱۰ پست در هر دوره = ۷۲ دوره تا یک چرخش کامل."""
    assert pub.effective_cooldown(720, 10) == 72


def test_cooldown_never_drops_below_the_floor(monkeypatch):
    """پول کوچک انتظار را به صفر نمی‌رساند؛ کفِ PUBLISH_COOLDOWN می‌ماند."""
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN", 6)
    assert pub.effective_cooldown(20, 10) == 6      # 20//10 = 2 → کف ۶
    assert pub.effective_cooldown(0, 10) == 6
    assert pub.effective_cooldown(720, 0) == 6


def test_cooldown_is_capped_so_memory_stays_bounded(monkeypatch):
    """بدون سقف، پولِ بزرگ یعنی کانفیگ هیچ‌وقت برنمی‌گردد و posted بی‌مرز رشد می‌کند."""
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN_MAX", 100)
    assert pub.effective_cooldown(90_000, 10) == 100


def test_cooldown_can_be_pinned_to_the_constant(monkeypatch):
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN_AUTO", False)
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN", 6)
    assert pub.effective_cooldown(720, 10) == 6


# ─── ندیده قبل از دیده‌شده ────────────────────────────────

def test_unseen_beats_a_better_ranked_config_out_of_cooldown(monkeypatch):
    """قلبِ اشکال: SLOW_IRAN رتبه‌ی بهتری دارد و انتظارش هم تمام شده، ولی
    MID_PLAIN هنوز هیچ‌وقت پست نشده — پس نوبت اوست.

    نسخه‌ی قبلی هر دو را در یک سطل می‌ریخت و با rank_key مرتب می‌کرد، پس
    کانفیگِ سرِ صف هر بار که انتظارش تمام می‌شد فوراً برمی‌گشت.
    """
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN_AUTO", False)
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN", 2)
    state = {"cycle": 9, "posted": {vless.short_id(SLOW_IRAN): 1}}
    assert pub.select_for_publish([SLOW_IRAN, MID_PLAIN], state, count=1) == [MID_PLAIN]


def test_seen_configs_only_fill_what_unseen_cannot(monkeypatch):
    """سطل ندیده اول تخلیه می‌شود، بعد سرد‌شده‌ها سهمیه را پر می‌کنند."""
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN_AUTO", False)
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN", 2)
    state = {"cycle": 9, "posted": {vless.short_id(SLOW_IRAN): 1}}
    chosen = pub.select_for_publish(
        [SLOW_IRAN, MID_PLAIN, FAST_PLAIN], state, count=3
    )
    assert chosen[-1] == SLOW_IRAN
    assert set(chosen[:2]) == {MID_PLAIN, FAST_PLAIN}


def test_whole_pool_is_shown_before_anything_repeats(monkeypatch):
    """قرارداد کاربر به‌صورت عدد: تا کل پول یک بار دیده نشود، تکرار نداریم."""
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN", 6)
    pool = [cfg(n, latency=float(n)) for n in range(20, 50)]   # ۳۰ کانفیگ
    state = {"cycle": 0, "posted": {}}
    seen: list = []
    for _ in range(10):            # ۱۰ دوره × ۳ = ۳۰ پست
        chosen = pub.select_for_publish(pool, state, count=3)
        seen.extend(vless.short_id(c) for c in chosen)
        state = pub.mark_published(
            state, chosen, pub.effective_cooldown(len(pool), 3)
        )
    assert len(seen) == 30 and len(set(seen)) == 30


def test_rotation_memory_does_not_grow_without_bound(monkeypatch):
    """هرس با انتظارِ *مؤثر* هم‌راستا است: نه زودتر (تکرار برمی‌گردد)، نه هرگز."""
    monkeypatch.setattr(pub, "PUBLISH_COOLDOWN", 2)
    state = {"cycle": 500, "posted": {"OLD1": 1, "OLD2": 480, "NEW": 499}}
    state = pub.mark_published(state, [FAST_PLAIN], cooldown=10)
    # horizon = 501 - 10*2 = 481 → OLD1 و OLD2 می‌روند، NEW می‌ماند
    assert set(state["posted"]) == {"NEW", vless.short_id(FAST_PLAIN)}
