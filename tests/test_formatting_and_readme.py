"""تست‌های src.tg_md و tools/update_readme.py."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import tg_md
from tools import update_readme


# ─── tg_md ────────────────────────────────────────────────

def test_strip_md_removes_markdown_specials():
    """کاراکترها حذف می‌شوند (نه جایگزین)، پس `[` می‌رود و `]` بی‌خطر است."""
    assert tg_md.strip_md("free_vpn *best* [x]`y`") == "freevpn best x]y"
    assert tg_md.strip_md("a\\_b") == "ab"


def test_strip_md_limit_adds_ellipsis():
    out = tg_md.strip_md("a" * 50, 10)
    assert len(out) == 10
    assert out.endswith("…")


def test_strip_md_accepts_non_string():
    assert tg_md.strip_md(ValueError("boom_x")) == "boomx"
    assert tg_md.strip_md(42) == "42"


def test_code_drops_backticks():
    assert tg_md.code("a`b") == "`ab`"


def test_truncate_keeps_code_span_balanced():
    text = "before `" + "x" * 100
    out = tg_md.truncate(text, 20)
    assert len(out) <= 21
    assert out.count("`") % 2 == 0


def test_truncate_noop_when_short():
    assert tg_md.truncate("short", 100) == "short"


# ─── update_readme ────────────────────────────────────────

STATS = {
    "timestamp": "2026-09-01T12:34:56+00:00",
    "duration_seconds": 91.2,
    "raw_collected": 4000,
    "valid_configs": 37,
    "pipeline": {
        "layer1_format": {"valid": 900},
        "layer2_dedup": {"unique": 500},
        "layer3_tcp": {"connected": 200},
        "layer4_tls": {"passed": 120},
        "layer5_geo": {"passed": 110},
        "layer6_http": {"passed": 37},
    },
}


def test_build_rows_uses_real_layer_keys():
    """کلید layer4_tls است نه layer4_xray — قبلاً همیشه خط تیره می‌شد."""
    rows = update_readme.build_rows(STATS)
    assert "| لایه ۴ TLS | 120 |" in rows
    assert "| لایه ۶ HTTP | 37 |" in rows
    assert update_readme.DASH not in rows
    assert "2026-09-01 12:34:56 UTC" in rows


def test_build_rows_tolerates_missing_pipeline():
    rows = update_readme.build_rows({})
    assert rows.count(update_readme.DASH) >= 7


def test_build_summary_without_stats():
    assert update_readme.build_summary({}) == "آمار موجود نیست\n"


def test_render_replaces_existing_block():
    content = (
        f"# T\n\n{update_readme.START}\nold junk\n{update_readme.END}\n\ntail\n"
    )
    out = update_readme.render(content, "NEW")
    assert "old junk" not in out
    assert f"{update_readme.START}\nNEW\n{update_readme.END}" in out
    assert out.endswith("tail\n")


def test_render_is_idempotent():
    content = f"{update_readme.START}\nNEW\n{update_readme.END}\n"
    assert update_readme.render(content, "NEW") == content


def test_render_appends_when_markers_missing():
    out = update_readme.render("# Only a title\n", "NEW")
    assert update_readme.START in out
    assert "NEW" in out
    assert out.count(update_readme.START) == 1


def test_template_contains_markers():
    assert update_readme.START in update_readme.README_TEMPLATE
    assert update_readme.END in update_readme.README_TEMPLATE


def test_load_stats_returns_dict_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(update_readme, "STATS_PATH", tmp_path / "nope.json")
    assert update_readme.load_stats() == {}


def test_load_stats_reads_file(tmp_path, monkeypatch):
    path = tmp_path / "stats.json"
    path.write_text(json.dumps(STATS), encoding="utf-8")
    monkeypatch.setattr(update_readme, "STATS_PATH", path)
    assert update_readme.load_stats()["valid_configs"] == 37


def test_load_stats_survives_corrupt_json(tmp_path, monkeypatch):
    path = tmp_path / "stats.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(update_readme, "STATS_PATH", path)
    assert update_readme.load_stats() == {}