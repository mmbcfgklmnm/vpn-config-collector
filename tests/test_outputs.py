"""تست لایه‌ی نوشتن خروجی (src/outputs.py).

قاعده‌ی اصلی این ماژول: هیچ فایلی با محتوای خالی نوشته نمی‌شود. یک اجرای
ناموفق نباید لینک subscription کاربران را خالی کند.
"""
import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import outputs, vless

UUID = "11111111-2222-3333-4444-555555555555"


def cfg(n: int, country: str = "NL", latency: float = 100.0, iran: float = 0.0) -> str:
    raw = f"vless://{UUID}@10.0.0.{n}:443?security=tls&type=tcp#node{n}"
    return vless.add_tag(raw, latency, country, iran)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """همه‌ی مسیرهای ماژول را به tmp_path می‌برد."""
    paths = {
        "CONFIGS_DIR": str(tmp_path),
        "ALL_FILE": str(tmp_path / "all.txt"),
        "VALID_FILE": str(tmp_path / "valid.txt"),
        "SUB_B64_FILE": str(tmp_path / "sub_base64.txt"),
        "IRAN_FILE": str(tmp_path / "iran.txt"),
        "IRAN_B64_FILE": str(tmp_path / "iran_base64.txt"),
        "INTL_FILE": str(tmp_path / "international.txt"),
        "INTL_B64_FILE": str(tmp_path / "international_base64.txt"),
        "POOL_FILE": str(tmp_path / "pool.txt"),
        "TOP_FILE": str(tmp_path / "top10.txt"),
        "INDEX_FILE": str(tmp_path / "index.json"),
        "HEALTH_FILE": str(tmp_path / "health.json"),
        "BY_COUNTRY_DIR": str(tmp_path / "countries"),
    }
    for name, value in paths.items():
        monkeypatch.setattr(outputs, name, value)
    return tmp_path


# ─── نوشتن پایه ───────────────────────────────────────────

def test_write_text_uses_lf_and_trailing_newline(tmp_path):
    """روی ویندوز پیش‌فرض CRLF است و بعضی کلاینت‌ها آن را نمی‌خوانند."""
    path = tmp_path / "a.txt"
    assert outputs.write_text(str(path), "x\ny") is True
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw == b"x\ny\n"


def test_write_text_refuses_empty_and_keeps_old_file(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"old\n")
    assert outputs.write_text(str(path), "   \n") is False
    assert path.read_bytes() == b"old\n"


def test_write_text_leaves_no_tmp_file(tmp_path):
    path = tmp_path / "a.txt"
    outputs.write_text(str(path), "x")
    assert not (tmp_path / "a.txt.tmp").exists()


def test_write_lines_drops_blanks(tmp_path):
    path = tmp_path / "a.txt"
    assert outputs.write_lines(str(path), ["a", "", "  ", " b "]) == 2
    assert path.read_text(encoding="utf-8") == "a\nb\n"


def test_write_lines_empty_creates_nothing(tmp_path):
    path = tmp_path / "a.txt"
    assert outputs.write_lines(str(path), ["", "   "]) == 0
    assert not path.exists()


def test_write_b64_round_trips(tmp_path):
    path = tmp_path / "b64.txt"
    items = [cfg(1), cfg(2)]
    assert outputs.write_b64(str(path), items) == 2
    decoded = base64.b64decode(path.read_text(encoding="utf-8")).decode("utf-8")
    assert decoded.strip().split("\n") == items


# ─── write_all ────────────────────────────────────────────

def test_write_all_empty_preserves_previous_output(sandbox):
    """اجرای خالی نباید valid.txt قبلی را پاک کند."""
    valid = sandbox / "valid.txt"
    valid.write_text("vless://old\n", encoding="utf-8")

    written = outputs.write_all([], {"pipeline": {}}, raw_configs=[])

    assert valid.read_text(encoding="utf-8") == "vless://old\n"
    assert written == {"valid": 0, "iran": 0, "international": 0,
                       "pool": 0, "top": 0, "countries": {}}
    # health.json برای فهمیدن *چرا* اجرا خالی بود همیشه نوشته می‌شود.
    assert json.loads((sandbox / "health.json").read_text(encoding="utf-8"))


def test_write_all_writes_every_output(sandbox):
    configs = [cfg(1, "NL", 100.0), cfg(2, "DE", 50.0)]
    written = outputs.write_all(configs, {"pipeline": {"layer1_format": {}}},
                                raw_configs=["vless://raw1", "vless://raw2"])

    assert written["valid"] == 2
    for name in ("all.txt", "valid.txt", "sub_base64.txt", "top10.txt",
                 "index.json", "health.json"):
        assert (sandbox / name).exists(), name
    # کم‌تأخیرترین اول
    lines = (sandbox / "valid.txt").read_text(encoding="utf-8").split("\n")
    assert vless.get_latency_ms(lines[0]) == 50.0


def test_write_all_skips_iran_files_when_no_iran_config(sandbox):
    written = outputs.write_all([cfg(1)], {})
    assert written["iran"] == 0
    assert not (sandbox / "iran.txt").exists()


def test_write_all_puts_iran_verified_on_top(sandbox):
    """ترتیب top باید همان ترتیب انتشار کانال باشد: ایران اول."""
    slow_iran = cfg(1, "NL", 400.0, iran=190.0)
    fast_plain = cfg(2, "DE", 20.0)
    written = outputs.write_all([fast_plain, slow_iran], {})

    assert written["iran"] == 1
    top = (sandbox / "top10.txt").read_text(encoding="utf-8").strip().split("\n")
    assert top[0] == slow_iran
    assert (sandbox / "iran_base64.txt").exists()


def test_write_all_index_is_machine_readable(sandbox):
    index = None
    outputs.write_all([cfg(1, "NL", 10.0, iran=150.0)], {"pipeline": {"x": 1}})
    index = json.loads((sandbox / "index.json").read_text(encoding="utf-8"))

    assert index["schema"] == 2
    assert index["counts"]["valid"] == 1
    assert index["counts"]["iran_verified"] == 1
    assert index["counts"]["countries"] == {"NL": 1}
    assert index["pipeline"] == {"x": 1}
    assert index["files"]["valid"].endswith("valid.txt")
    # قرارداد تازه: مصرف‌کننده باید بتواند لینک داخلی/خارجی و پول ذخیره را
    # از همین فایل پیدا کند، نه با حدس زدن مسیر.
    assert index["files"]["international"].endswith("international.txt")
    assert index["files"]["pool"].endswith("pool.txt")
    assert "international" in index["counts"]
    assert "pool" in index["counts"]


# ─── تفکیک داخلی/خارجی و پول ذخیره ────────────────────────

def test_write_all_splits_domestic_and_international(sandbox):
    """کاربر داخل ایران نباید نصف فهرست را بی‌فایده امتحان کند."""
    iran_cfg = cfg(1, "NL", 300.0, iran=180.0)
    intl_cfg = cfg(2, "DE", 40.0)
    written = outputs.write_all([intl_cfg, iran_cfg], {})

    assert written["iran"] == 1
    assert written["international"] == 1
    assert (sandbox / "iran.txt").read_text(encoding="utf-8").strip() == iran_cfg
    assert (sandbox / "international.txt").read_text(encoding="utf-8").strip() == intl_cfg
    assert (sandbox / "international_base64.txt").exists()
    # فهرست کامل هر دو را دارد
    assert len((sandbox / "valid.txt").read_text(encoding="utf-8").strip().split("\n")) == 2


def test_pool_is_written_even_when_run_has_no_verified_config(sandbox):
    """اجرای بی‌خروجی همان جایی است که کانال به ذخیره نیاز دارد."""
    written = outputs.write_all([], {}, pool_configs=[cfg(7, "DE", 60.0)])

    assert written["pool"] == 1
    assert (sandbox / "pool.txt").exists()
    # و هیچ فایل خروجی اصلی ساخته نشده
    assert not (sandbox / "valid.txt").exists()


def test_pool_is_deduped_and_sorted_by_latency(sandbox):
    slow, fast = cfg(1, "NL", 900.0), cfg(2, "DE", 30.0)
    outputs.write_all([cfg(3, "FR", 10.0)], {}, pool_configs=[slow, fast, slow])
    lines = (sandbox / "pool.txt").read_text(encoding="utf-8").strip().split("\n")
    assert lines == [fast, slow]


def test_pool_never_mixes_into_valid_output(sandbox):
    """ذخیره تست‌نشده است؛ نباید در لینک اشتراک تأییدشده بنشیند."""
    reserve = cfg(9, "DE", 25.0)
    outputs.write_all([cfg(1, "NL", 500.0)], {}, pool_configs=[reserve])
    assert reserve not in (sandbox / "valid.txt").read_text(encoding="utf-8")
    assert reserve not in (sandbox / "top10.txt").read_text(encoding="utf-8")


# ─── تفکیک کشور ───────────────────────────────────────────

def test_group_by_country_uses_xx_for_unknown():
    raw = f"vless://{UUID}@10.0.0.9:443?security=tls#bare"
    groups = outputs.group_by_country([raw])
    assert list(groups) == ["XX"]


def test_write_countries_removes_stale_files(sandbox):
    outputs.write_all([cfg(1, "NL"), cfg(2, "DE")], {})
    assert (sandbox / "countries" / "NL.txt").exists()

    # اجرای بعدی هلند ندارد؛ فایل کهنه نباید به‌عنوان خروجی تازه بماند.
    outputs.write_all([cfg(2, "DE")], {})
    assert not (sandbox / "countries" / "NL.txt").exists()
    assert (sandbox / "countries" / "DE.txt").exists()
