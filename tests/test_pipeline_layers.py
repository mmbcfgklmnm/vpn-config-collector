"""تست‌های لایه ۱ (فرمت) و لایه ۲ (حذف تکراری)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tester.deduplicator import deduplicate, get_fingerprint, normalize
from src.tester.format_validator import filter_by_format, validate_host, validate_vless

UUID = "11111111-2222-3333-4444-555555555555"


def cfg(host="1.2.3.4", port=443, uuid=UUID, query="security=reality&pbk=ABC",
        name="node"):
    return f"vless://{uuid}@{host}:{port}?{query}#{name}"


# ─── لایه ۱ ───────────────────────────────────────────────

def test_valid_reality_and_tls_pass():
    assert validate_vless(cfg())[0] is True
    assert validate_vless(cfg(query="security=tls&type=ws&path=%2Fws"))[0] is True
    assert validate_vless(cfg(query="security=xtls&flow=xtls-rprx-vision"))[0] is True


def test_reality_without_pbk_rejected():
    ok, reason = validate_vless(cfg(query="security=reality"))
    assert ok is False
    assert "pbk" in reason


def test_plain_security_rejected():
    ok, reason = validate_vless(cfg(query="security=none"))
    assert ok is False
    assert "security" in reason


def test_missing_security_rejected():
    """بدون پارامتر security مقدار پیش‌فرض none است."""
    assert validate_vless(cfg(query="type=tcp"))[0] is False


def test_bad_uuid_rejected():
    assert validate_vless(cfg(uuid="not-a-uuid"))[0] is False


def test_dummy_uuid_rejected():
    ok, reason = validate_vless(cfg(uuid="00000000-0000-0000-0000-000000000000"))
    assert ok is False
    assert reason == "UUID dummy"


def test_bad_port_rejected():
    assert validate_vless(cfg(port=99999))[0] is False


def test_wrong_scheme_rejected():
    assert validate_vless(f"vmess://{UUID}@1.2.3.4:443?security=tls")[0] is False


def test_validate_host_accepts_ipv4_ipv6_domain():
    assert validate_host("8.8.8.8") is True
    assert validate_host("2001:db8::1") is True
    assert validate_host("sub.example.co.uk") is True
    assert validate_host("") is False
    assert validate_host("not_a_host") is False


def test_filter_by_format_stats():
    configs = [cfg(), cfg(query="security=none"), "garbage"]
    valid, stats = filter_by_format(configs)
    assert len(valid) == 1
    assert stats == {
        "total": 3,
        "valid": 1,
        "invalid": 2,
        "top_reasons": stats["top_reasons"],
    }
    assert sum(stats["top_reasons"].values()) == 2


# ─── لایه ۲ ───────────────────────────────────────────────

def test_exact_duplicate_removed():
    unique, stats = deduplicate([cfg(), cfg()])
    assert len(unique) == 1
    assert stats["exact_dups"] == 1


def test_name_only_difference_is_duplicate():
    unique, stats = deduplicate([cfg(name="a"), cfg(name="b")])
    assert len(unique) == 1
    assert stats["norm_dups"] == 1


def test_param_order_difference_is_duplicate():
    a = cfg(query="security=reality&pbk=ABC&sni=x.com")
    b = cfg(query="sni=x.com&security=reality&pbk=ABC")
    unique, _ = deduplicate([a, b])
    assert len(unique) == 1


def test_transport_difference_is_kept():
    """باگ قبلی: ws و tcp روی یک host:port:uuid یکی شمرده می‌شدند."""
    tcp = cfg(query="security=tls&type=tcp")
    ws = cfg(query="security=tls&type=ws&path=%2Fws")
    unique, stats = deduplicate([tcp, ws])
    assert len(unique) == 2
    assert stats["fp_dups"] == 0


def test_path_case_is_significant():
    lower = cfg(query="security=tls&type=ws&path=%2Fws")
    upper = cfg(query="security=tls&type=ws&path=%2FWS")
    assert normalize(lower) != normalize(upper)
    assert get_fingerprint(lower) != get_fingerprint(upper)
    assert len(deduplicate([lower, upper])[0]) == 2


def test_different_sni_is_kept():
    a = cfg(query="security=reality&pbk=ABC&sni=a.com")
    b = cfg(query="security=reality&pbk=ABC&sni=b.com")
    assert len(deduplicate([a, b])[0]) == 2


def test_unparsable_configs_deduped_by_hash():
    unique, _ = deduplicate(["garbage", "garbage", "other"])
    assert len(unique) == 2


def test_blank_lines_skipped():
    unique, stats = deduplicate(["", "   ", cfg()])
    assert len(unique) == 1
    assert stats["total"] == 3
