"""تست صف اهدای کاربران (src/donations.py).

دو قرارداد صریح کاربر این‌جا تست می‌شوند:
  ۱. هر کانفیگ اهدایی *حداکثر یک بار* پست می‌شود.
  ۲. داده‌ی کاربر لو نمی‌رود — شناسه‌ی تلگرام روی دیسک ذخیره نمی‌شود.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import donations

UUID = "3f8b1c2a-9d4e-4a7b-8c1d-2e3f4a5b6c7d"


def donation(n: int, host: str = "") -> str:
    host = host or f"edge{n}.example.com"
    return (
        f"vless://{UUID}@{host}:443"
        f"?security=tls&sni={host}&type=ws&path=%2Fx#my-own-name-{n}"
    )


@pytest.fixture(autouse=True)
def isolated_salt(monkeypatch):
    """salt تستی. بدون این، هر تستی که donor_hash صدا می‌زند فایل
    configs/.donate_salt واقعیِ مخزن را می‌سازد یا می‌خواند."""
    monkeypatch.setenv("DONATE_SALT", "test-salt-not-a-secret")


@pytest.fixture()
def queue(tmp_path, monkeypatch):
    """صف را به tmp_path می‌برد و محدودیت‌ها را قابل‌پیش‌بینی می‌کند."""
    monkeypatch.setattr(donations, "DONATIONS_FILE", str(tmp_path / "donations.json"))
    monkeypatch.setattr(donations, "DONATE_MIN_GAP_SEC", 0)
    monkeypatch.setattr(donations, "DONATE_MAX_PER_MSG", 20)
    monkeypatch.setattr(donations, "DONATE_MAX_PER_DAY", 50)
    monkeypatch.setattr(donations, "DONATE_QUEUE_MAX", 100)
    return tmp_path / "donations.json"


# ─── اعتبارسنجی ───────────────────────────────────────────

def test_donor_chosen_name_is_discarded():
    """اسم اهداکننده در کانال رندر می‌شود؛ ورودی کاربر نباید به متن برسد."""
    clean, reason = donations.sanitize(donation(1))
    assert reason == ""
    assert "my-own-name" not in clean
    assert "#Donated-" in clean


def test_markdown_and_entity_bait_cannot_survive_in_the_name():
    clean, _ = donations.sanitize(
        f"vless://{UUID}@a.example.com:443?security=tls&sni=a.example.com"
        "#*bold*[link](http://evil)`code`"
    )
    for ch in ("*", "[", "]", "(", ")", "`"):
        assert ch not in clean


@pytest.mark.parametrize("bad", [
    "",
    "   ",
    "not-a-config",
    "vmess://something",
    f"vless://{UUID}@127.0.0.1:443?security=tls&sni=a.com",
    f"vless://{UUID}@10.0.0.5:443?security=tls&sni=a.com",
    f"vless://{UUID}@169.254.169.254:80?security=tls&sni=a.com",
    f"vless://{UUID}@localhost:443?security=tls&sni=a.com",
    f"vless://{UUID}@printer.local:443?security=tls&sni=a.com",
    f"vless://{UUID}@db.internal:443?security=tls&sni=a.com",
])
def test_dangerous_or_broken_donations_are_rejected(bad):
    """کانفیگ اهدایی به تست TCP ما می‌رسد — نباید بشود آن را به شبکه‌ی
    داخلی رانر نشانه گرفت."""
    clean, reason = donations.sanitize(bad)
    assert clean == ""
    assert reason


def test_oversized_and_multiline_input_rejected():
    assert donations.sanitize("vless://" + "a" * 2000)[0] == ""
    assert donations.sanitize(donation(1) + "\nvless://second")[0] == ""


# ─── افزودن ───────────────────────────────────────────────

def test_add_counts_added_duplicate_and_invalid(queue):
    result = donations.add(
        [donation(1), donation(2), donation(1), "garbage"], user_id=555
    )
    assert result["added"] == 2
    assert result["duplicate"] == 1
    assert result["invalid"] == 1
    assert result["queued_total"] == 2
    assert result["reasons"]


def test_duplicate_across_users_is_rejected(queue):
    donations.add([donation(1)], user_id=1)
    result = donations.add([donation(1)], user_id=2)
    assert result["added"] == 0
    assert result["duplicate"] == 1


def test_per_message_cap_is_enforced(queue, monkeypatch):
    monkeypatch.setattr(donations, "DONATE_MAX_PER_MSG", 2)
    result = donations.add([donation(i) for i in range(5)], user_id=7)
    assert result["added"] == 2


def test_daily_cap_blocks_further_donations(queue, monkeypatch):
    monkeypatch.setattr(donations, "DONATE_MAX_PER_DAY", 2)
    donations.add([donation(1), donation(2)], user_id=7)
    result = donations.add([donation(3)], user_id=7)
    assert result["added"] == 0
    assert "روزانه" in result["blocked"]


def test_min_gap_blocks_spam(queue, monkeypatch):
    monkeypatch.setattr(donations, "DONATE_MIN_GAP_SEC", 300)
    donations.add([donation(1)], user_id=7)
    result = donations.add([donation(2)], user_id=7)
    assert result["added"] == 0
    assert result["blocked"]
    # کاربر دیگری مسدود نمی‌شود
    assert donations.add([donation(3)], user_id=8)["added"] == 1


def test_queue_cap_blocks_when_full(queue, monkeypatch):
    monkeypatch.setattr(donations, "DONATE_QUEUE_MAX", 1)
    donations.add([donation(1)], user_id=7)
    result = donations.add([donation(2)], user_id=8)
    assert result["added"] == 0
    assert result["blocked"]


# ─── حریم خصوصی ───────────────────────────────────────────

def test_telegram_id_is_never_stored(queue):
    donations.add([donation(1)], user_id=987654321)
    raw = queue.read_text(encoding="utf-8")
    assert "987654321" not in raw
    data = json.loads(raw)
    assert list(data["donors"]) == [donations.donor_hash(987654321)]


def test_donor_hash_is_stable_and_short():
    a, b = donations.donor_hash(42), donations.donor_hash(42)
    assert a == b and len(a) == 12
    assert donations.donor_hash(43) != a


def test_stats_exposes_no_donor_identity(queue):
    donations.add([donation(1)], user_id=42)
    assert set(donations.stats()) == {
        "queued", "taken", "sent", "total", "donors", "cycles_left",
    }


# ─── حداکثر یک بار ────────────────────────────────────────

def test_take_marks_taken_and_never_repeats(queue):
    donations.add([donation(i) for i in range(4)], user_id=7)

    first = donations.take_for_cycle(2)
    assert len(first) == 2
    assert donations.stats()["taken"] == 2

    second = donations.take_for_cycle(2)
    assert set(first).isdisjoint(second)
    assert donations.take_for_cycle(2) == []      # صف تمام شد


def test_sent_config_is_not_reofferd_after_restart(queue):
    donations.add([donation(1)], user_id=7)
    taken = donations.take_for_cycle(1)
    assert donations.mark_sent(taken) == 1
    assert donations.stats()["sent"] == 1
    # حتی اگر همان کانفیگ دوباره اهدا شود، تکراری است
    assert donations.add([donation(1)], user_id=9)["duplicate"] == 1
    assert donations.take_for_cycle(1) == []


def test_taken_items_are_not_auto_requeued(queue):
    """اگر پروسه بین برداشت و ارسال بمیرد، خودکار برنمی‌گردد — ممکن است
    واقعاً پست شده باشد و قرارداد «یک بار» می‌شکست."""
    donations.add([donation(1)], user_id=7)
    donations.take_for_cycle(1)
    assert donations.take_for_cycle(1) == []
    assert donations.requeue_taken() == 1        # فقط دستیِ ادمین
    assert len(donations.take_for_cycle(1)) == 1


def test_take_returns_nothing_when_state_cannot_be_saved(queue, monkeypatch):
    """پست کردن با علامتِ ذخیره‌نشده = ریسک ارسال دوباره در دوره‌ی بعد."""
    donations.add([donation(1)], user_id=7)
    monkeypatch.setattr(donations, "_save", lambda data: False)
    assert donations.take_for_cycle(1) == []


def test_take_zero_or_empty_queue_is_safe(queue):
    assert donations.take_for_cycle(0) == []
    assert donations.take_for_cycle(3) == []
    assert donations.mark_sent([]) == 0


def test_queue_is_fifo(queue):
    donations.add([donation(1)], user_id=1)
    donations.add([donation(2)], user_id=2)
    assert donations.take_for_cycle(1)[0] == donations.sanitize(donation(1))[0]


# ─── ذخیره‌سازی ────────────────────────────────────────────

def test_corrupt_queue_file_does_not_crash(queue):
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text("{not json", encoding="utf-8")
    assert donations.queued_count() == 0
    assert donations.add([donation(1)], user_id=7)["added"] == 1


def test_wrong_types_in_queue_file_are_ignored(queue):
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(
        json.dumps({"items": "nope", "donors": [1, 2]}), encoding="utf-8"
    )
    assert donations.stats()["total"] == 0
    assert donations.add([donation(1)], user_id=7)["added"] == 1


def test_queue_file_uses_lf(queue):
    donations.add([donation(1)], user_id=7)
    assert b"\r\n" not in queue.read_bytes()


def test_purge_sent_keeps_recent_records(queue):
    donations.add([donation(i) for i in range(4)], user_id=7)
    donations.mark_sent(donations.take_for_cycle(3))
    assert donations.purge_sent(keep=1) == 2
    stats = donations.stats()
    assert stats["sent"] == 1
    assert stats["queued"] == 1


def test_purge_sent_noop_when_under_limit(queue):
    donations.add([donation(1)], user_id=7)
    assert donations.purge_sent(keep=100) == 0
