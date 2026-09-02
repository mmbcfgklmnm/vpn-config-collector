"""تست‌های لایه ۱ (فرمت) و لایه ۲ (حذف تکراری)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tester import format_validator
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
    assert set(stats) == {"total", "valid", "invalid", "cdn_plain", "top_reasons"}
    assert (stats["total"], stats["valid"], stats["invalid"]) == (3, 1, 2)
    assert stats["cdn_plain"] == 0
    assert sum(stats["top_reasons"].values()) == 2


# ─── استثنای CDN-plain (لایه ۱) ───────────────────────────
# چرا این استثنا هست: security=none تنها دلیلِ رد شدن ~۸۰۰ کانفیگ بود، در
# حالی که همین دسته (IP کلادفلر + ws) بالاترین نرخ دسترسی از ایران را داشت.

CF_IP = "104.17.5.5"


def test_cdn_plain_ws_on_cf_ip_accepted():
    ok, reason = validate_vless(
        cfg(host=CF_IP, query="type=ws&security=none&path=%2Fws")
    )
    assert ok is True, reason


def test_cdn_plain_needs_http_transport():
    """روی همان IP کلادفلر ولی با transport tcp — الگوی CF نیست."""
    assert validate_vless(
        cfg(host=CF_IP, query="type=tcp&security=none")
    )[0] is False


def test_cdn_plain_rejects_raw_vps_ip():
    """IP خام با ws هم رد می‌شود؛ استثنا فقط برای endpoint پشت CDN است."""
    assert validate_vless(
        cfg(host="1.2.3.4", query="type=ws&security=none&path=%2Fws")
    )[0] is False


def test_cdn_plain_domain_needs_standard_cf_port():
    on_cf_port = cfg(host="a.example.com", port=8080,
                     query="type=ws&security=none&path=%2Fws")
    off_cf_port = cfg(host="a.example.com", port=12345,
                      query="type=ws&security=none&path=%2Fws")
    assert validate_vless(on_cf_port)[0] is True
    assert validate_vless(off_cf_port)[0] is False


def test_cdn_plain_counted_in_stats():
    configs = [cfg(), cfg(host=CF_IP, query="type=ws&security=none&path=%2Fws")]
    valid, stats = filter_by_format(configs)
    assert len(valid) == 2
    assert stats["cdn_plain"] == 1


def test_cdn_plain_can_be_disabled(monkeypatch):
    """با ALLOW_CDN_PLAIN=0 رفتار به نسخه‌ی سخت‌گیر قبلی برمی‌گردد."""
    monkeypatch.setattr(format_validator, "ALLOW_CDN_PLAIN", False)
    assert validate_vless(
        cfg(host=CF_IP, query="type=ws&security=none&path=%2Fws")
    )[0] is False


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
