"""تست تازگی منبع کانفیگ در ربات — روی Railway فایل محلی کهنه است."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot

UUID = "11111111-2222-3333-4444-555555555555"
LOCAL = [f"vless://{UUID}@10.0.0.1:443?security=tls#old"]
REMOTE = [
    f"vless://{UUID}@10.0.0.2:443?security=tls#new1",
    f"vless://{UUID}@10.0.0.3:443?security=tls#new2",
]

OLD_TS = "2026-09-01T10:00:00+00:00"
NEW_TS = "2026-09-01T20:14:59+00:00"


def _write_local(tmp_path, monkeypatch, configs, timestamp):
    valid = tmp_path / "valid.txt"
    stats = tmp_path / "stats.json"
    valid.write_text("\n".join(configs) + "\n", encoding="utf-8")
    stats.write_text(
        json.dumps({"timestamp": timestamp, "valid_configs": len(configs)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "VALID_FILE", str(valid))
    monkeypatch.setattr(bot, "STATS_FILE", str(stats))


def _set_remote(monkeypatch, configs, timestamp):
    monkeypatch.setattr(bot, "_configs_cache", list(configs))
    monkeypatch.setattr(
        bot, "_stats_cache",
        {"timestamp": timestamp, "valid_configs": len(configs)} if configs else {},
    )


def test_remote_wins_when_newer(tmp_path, monkeypatch):
    """باگ اصلی: snapshot زمان deploy همیشه برنده می‌شد."""
    _write_local(tmp_path, monkeypatch, LOCAL, OLD_TS)
    _set_remote(monkeypatch, REMOTE, NEW_TS)
    assert bot.load_configs() == REMOTE
    assert bot.load_stats()["timestamp"] == NEW_TS


def test_local_wins_when_newer(tmp_path, monkeypatch):
    """اجرای محلی pipeline نباید با cache قدیمی گیت‌هاب پوشیده شود."""
    _write_local(tmp_path, monkeypatch, LOCAL, NEW_TS)
    _set_remote(monkeypatch, REMOTE, OLD_TS)
    assert bot.load_configs() == LOCAL
    assert bot.load_stats()["timestamp"] == NEW_TS


def test_local_used_when_no_remote(tmp_path, monkeypatch):
    _write_local(tmp_path, monkeypatch, LOCAL, OLD_TS)
    _set_remote(monkeypatch, [], "")
    assert bot.load_configs() == LOCAL


def test_remote_used_when_local_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "VALID_FILE", str(tmp_path / "nope.txt"))
    monkeypatch.setattr(bot, "STATS_FILE", str(tmp_path / "nostats.json"))
    _set_remote(monkeypatch, REMOTE, NEW_TS)
    assert bot.load_configs() == REMOTE
    assert bot.load_stats()["timestamp"] == NEW_TS


def test_empty_everywhere_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "VALID_FILE", str(tmp_path / "nope.txt"))
    monkeypatch.setattr(bot, "STATS_FILE", str(tmp_path / "nostats.json"))
    _set_remote(monkeypatch, [], "")
    assert bot.load_configs() == []
    assert bot.load_stats() == {}


def test_local_without_stats_loses_to_remote(tmp_path, monkeypatch):
    """فایل بدون stats.json تاریخ ندارد، پس cache تازه ترجیح داده می‌شود."""
    valid = tmp_path / "valid.txt"
    valid.write_text("\n".join(LOCAL) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "VALID_FILE", str(valid))
    monkeypatch.setattr(bot, "STATS_FILE", str(tmp_path / "nostats.json"))
    _set_remote(monkeypatch, REMOTE, NEW_TS)
    assert bot.load_configs() == REMOTE
